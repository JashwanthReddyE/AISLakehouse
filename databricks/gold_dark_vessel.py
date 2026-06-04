# Databricks notebook source
# MAGIC %md
# MAGIC # Gold — dark-vessel detection
# MAGIC
# MAGIC Batch analytic over **silver/positions**: for each vessel, find gaps between consecutive
# MAGIC position reports that exceed a threshold (the vessel went silent then reappeared), and
# MAGIC score how anomalous each gap is vs that vessel's own normal cadence.
# MAGIC
# MAGIC Output **gold/dark_events** (full recompute each run — idempotent overwrite).
# MAGIC
# MAGIC > **Caveat (modeled, not a bug):** terrestrial AIS has real coverage dead zones, and sparse
# MAGIC > ingestion creates gaps too. A dark event is a *candidate* signal, not proof of intent.
# MAGIC > Mirrors the unit-tested `gold/dark_vessel.py`.

# COMMAND ----------

dbutils.widgets.text("storage_account", "", "ADLS Gen2 storage account name")
dbutils.widgets.text("lake_container", "lakehouse", "Lake container")
dbutils.widgets.text("gap_threshold_s", "1800", "Dark-gap threshold (seconds)")
dbutils.widgets.text("normal_multiplier", "4.0", "Gap x normal cadence -> confidence 1.0")

STORAGE = dbutils.widgets.get("storage_account")
CONTAINER = dbutils.widgets.get("lake_container")
THRESHOLD = float(dbutils.widgets.get("gap_threshold_s"))
MULT = float(dbutils.widgets.get("normal_multiplier"))

base = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net"
SILVER_POS = f"{base}/silver/positions"
GOLD_DARK = f"{base}/gold/dark_events"

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

positions = spark.read.format("delta").load(SILVER_POS)

w = Window.partitionBy("mmsi").orderBy("event_time")

# Previous report per vessel -> gap + last-known position before the gap.
seq = (
    positions.select("mmsi", "flag_country", "event_time", "latitude", "longitude", "sog")
    .withColumn("prev_time", F.lag("event_time").over(w))
    .withColumn("prev_lat", F.lag("latitude").over(w))
    .withColumn("prev_lon", F.lag("longitude").over(w))
    .withColumn("prev_sog", F.lag("sog").over(w))
    .withColumn(
        "gap_seconds",
        F.col("event_time").cast("long") - F.col("prev_time").cast("long"),
    )
    .where("prev_time IS NOT NULL")
)

# Per-vessel "normal" cadence = median of non-dark gaps (fallback to threshold).
normal = (
    seq.where(F.col("gap_seconds") <= THRESHOLD)
    .groupBy("mmsi")
    .agg(F.expr("percentile_approx(gap_seconds, 0.5)").alias("normal_gap_seconds"))
)

dark = (
    seq.where(F.col("gap_seconds") > THRESHOLD)
    .join(normal, "mmsi", "left")
    .withColumn("normal_gap_seconds", F.coalesce("normal_gap_seconds", F.lit(THRESHOLD)))
    .withColumn(
        "confidence",
        F.round(
            F.least(
                F.lit(1.0),
                F.col("gap_seconds") / (F.lit(MULT) * F.greatest(F.col("normal_gap_seconds"), F.lit(1.0))),
            ),
            4,
        ),
    )
    .select(
        "mmsi",
        "flag_country",
        F.col("prev_time").alias("dark_start"),
        F.col("event_time").alias("dark_end"),
        "gap_seconds",
        F.round(F.col("gap_seconds") / 60.0, 1).alias("gap_minutes"),
        F.col("prev_lat").alias("last_latitude"),
        F.col("prev_lon").alias("last_longitude"),
        F.col("prev_sog").alias("last_sog"),
        "normal_gap_seconds",
        "confidence",
        F.current_timestamp().alias("gold_computed_ts"),
    )
)

# COMMAND ----------

dark.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(GOLD_DARK)

# COMMAND ----------

import json

events = spark.read.format("delta").load(GOLD_DARK)
n_events = events.count()
n_vessels = events.select("mmsi").distinct().count()
summary = events.agg(
    F.round(F.avg("confidence"), 3).alias("avg_conf"),
    F.round(F.max("gap_minutes"), 1).alias("max_gap_min"),
).collect()[0]

print(f"dark_events={n_events}  vessels={n_vessels}  avg_confidence={summary['avg_conf']}  "
      f"max_gap_min={summary['max_gap_min']}")
display(events.orderBy(F.desc("confidence"), F.desc("gap_minutes")).limit(15))

dbutils.notebook.exit(
    json.dumps(
        {
            "dark_events": n_events,
            "vessels_affected": n_vessels,
            "avg_confidence": summary["avg_conf"],
            "max_gap_minutes": summary["max_gap_min"],
            "gap_threshold_s": THRESHOLD,
        }
    )
)
