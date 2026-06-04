# Databricks notebook source
# MAGIC %md
# MAGIC # Gold — tanker flow & floating-storage indicators (commodity proxy)
# MAGIC
# MAGIC AIS as commodity *intelligence* (NOT a price predictor):
# MAGIC * classify tankers via AIS ship-type (80-89) from `silver/ship_static`,
# MAGIC * **tanker flow**: hourly distinct-tanker transit count through the strait,
# MAGIC * **floating storage**: tankers clustered within a small radius at ~0 speed for a long
# MAGIC   span = idle/storing offshore = a classic crude-oversupply (bearish) signal.
# MAGIC
# MAGIC Outputs `gold/tanker_flow` and `gold/floating_storage`. Mirrors unit-tested `gold/tanker.py`.
# MAGIC
# MAGIC > Honest scope: one chokepoint + short history = illustrative indicator, not market-grade.

# COMMAND ----------

dbutils.widgets.text("storage_account", "", "ADLS Gen2 storage account name")
dbutils.widgets.text("lake_container", "lakehouse", "Lake container")
dbutils.widgets.text("fs_radius_nm", "2.0", "Floating-storage max radius (nm)")
dbutils.widgets.text("fs_min_span_hours", "1.0", "Floating-storage min span (hours)")
dbutils.widgets.text("fs_max_avg_sog", "1.0", "Floating-storage max avg SOG (knots)")
dbutils.widgets.text("fs_min_points", "5", "Floating-storage min points")

STORAGE = dbutils.widgets.get("storage_account")
CONTAINER = dbutils.widgets.get("lake_container")
FS_RADIUS = float(dbutils.widgets.get("fs_radius_nm"))
FS_SPAN_H = float(dbutils.widgets.get("fs_min_span_hours"))
FS_SOG = float(dbutils.widgets.get("fs_max_avg_sog"))
FS_PTS = int(dbutils.widgets.get("fs_min_points"))

base = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net"
SILVER_POS = f"{base}/silver/positions"
SILVER_STATIC = f"{base}/silver/ship_static"
GOLD_FLOW = f"{base}/gold/tanker_flow"
GOLD_FS = f"{base}/gold/floating_storage"

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.functions import broadcast

positions = spark.read.format("delta").load(SILVER_POS)
static = spark.read.format("delta").load(SILVER_STATIC)

# Tanker MMSIs from static broadcasts (AIS ship-type 80-89). Latest type wins.
tankers = (
    static.where("ship_type BETWEEN 80 AND 89")
    .groupBy("mmsi")
    .agg(F.max("ship_type").alias("ship_type"))
)

# Label positions as tanker (left join the small tanker set).
pos = (
    positions.join(broadcast(tankers.select("mmsi").withColumn("is_tanker", F.lit(True))), "mmsi", "left")
    .withColumn("is_tanker", F.coalesce("is_tanker", F.lit(False)))
)

# COMMAND ----------
# Tanker flow: hourly distinct vessels vs distinct tankers (throughput proxy).

flow = (
    pos.withColumn("hour", F.date_trunc("hour", "event_time"))
    .groupBy("hour")
    .agg(
        F.countDistinct("mmsi").alias("vessels"),
        F.countDistinct(F.when(F.col("is_tanker"), F.col("mmsi"))).alias("tankers"),
    )
    .withColumn(
        "tanker_share",
        F.round(F.col("tankers") / F.greatest(F.col("vessels"), F.lit(1)), 3),
    )
    .orderBy("hour")
)
flow.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(GOLD_FLOW)

# COMMAND ----------
# Floating storage: per-tanker spatial spread + dwell + speed.

tpos = pos.where("is_tanker")
fs = (
    tpos.groupBy("mmsi", "flag_country")
    .agg(
        F.min("event_time").alias("first_seen"),
        F.max("event_time").alias("last_seen"),
        F.min("latitude").alias("min_lat"),
        F.max("latitude").alias("max_lat"),
        F.min("longitude").alias("min_lon"),
        F.max("longitude").alias("max_lon"),
        F.round(F.avg("sog"), 2).alias("avg_sog"),
        F.count("*").alias("n_points"),
    )
    .withColumn("span_hours", F.round((F.col("last_seen").cast("long") - F.col("first_seen").cast("long")) / 3600.0, 2))
    .withColumn("center_lat", (F.col("min_lat") + F.col("max_lat")) / 2.0)
    # Bounding-box diagonal in nm (mirrors gold/tanker.py bbox_diagonal_nm).
    .withColumn(
        "spread_nm",
        F.round(
            F.sqrt(
                F.pow((F.col("max_lat") - F.col("min_lat")) * 60.0, 2)
                + F.pow((F.col("max_lon") - F.col("min_lon")) * 60.0 * F.cos(F.radians("center_lat")), 2)
            ),
            3,
        ),
    )
    .withColumn(
        "is_floating_storage",
        (F.col("spread_nm") <= FS_RADIUS)
        & (F.col("span_hours") >= FS_SPAN_H)
        & (F.col("avg_sog").isNotNull() & (F.col("avg_sog") <= FS_SOG))
        & (F.col("n_points") >= FS_PTS),
    )
    .withColumn("gold_computed_ts", F.current_timestamp())
    .select(
        "mmsi", "flag_country", "first_seen", "last_seen", "span_hours",
        "center_lat", "spread_nm", "avg_sog", "n_points", "is_floating_storage", "gold_computed_ts",
    )
)
fs.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(GOLD_FS)

# COMMAND ----------

import json

flow_df = spark.read.format("delta").load(GOLD_FLOW)
fs_df = spark.read.format("delta").load(GOLD_FS)

distinct_tankers = tankers.count()
fs_candidates = fs_df.where("is_floating_storage").count()
avg_share = flow_df.agg(F.round(F.avg("tanker_share"), 3).alias("s")).collect()[0]["s"]

print(f"distinct_tankers={distinct_tankers}  floating_storage_candidates={fs_candidates}  "
      f"avg_tanker_share={avg_share}")
display(flow_df)
display(fs_df.orderBy(F.desc("is_floating_storage"), F.asc("spread_nm")).limit(15))

dbutils.notebook.exit(
    json.dumps(
        {
            "distinct_tankers": distinct_tankers,
            "tanker_positions": tpos.count(),
            "floating_storage_candidates": fs_candidates,
            "avg_tanker_share": avg_share,
            "flow_hours": flow_df.count(),
        }
    )
)
