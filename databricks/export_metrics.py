# Databricks notebook source
# MAGIC %md
# MAGIC # Export lakehouse metrics -> JSON (for the static dashboard)
# MAGIC
# MAGIC Reads every layer and emits a compact JSON summary via `dbutils.notebook.exit`.
# MAGIC `serving/build_dashboard.py` renders that JSON into a self-contained `dashboard.html`.

# COMMAND ----------

dbutils.widgets.text("storage_account", "", "ADLS Gen2 storage account name")
dbutils.widgets.text("lake_container", "lakehouse", "Lake container")
STORAGE = dbutils.widgets.get("storage_account")
CONTAINER = dbutils.widgets.get("lake_container")
base = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net"

# COMMAND ----------

import json

from pyspark.sql import functions as F
from pyspark.sql.window import Window


def load(path):
    return spark.read.format("delta").load(f"{base}/{path}")


def count(path):
    try:
        return load(path).count()
    except Exception:
        return 0


bronze_n = count("bronze/ais_raw")
pos = load("silver/positions")
quar = load("silver/quarantine")
static = load("silver/ship_static")
gold = load("gold/dark_events")

pos_n, quar_n, static_n, gold_n = pos.count(), quar.count(), static.count(), gold.count()
total_pos = pos_n + quar_n


def rows(df, n=10):
    return [r.asDict() for r in df.limit(n).collect()]


def iso(df, col):
    v = df.agg(F.max(col).alias("m")).collect()[0]["m"]
    return v.isoformat() if v is not None else None


# Commodity indicators (tanker flow + floating storage). Optional — skip if not yet built.
try:
    flow = load("gold/tanker_flow")
    fs = load("gold/floating_storage")
    commodity = {
        "distinct_tankers": fs.count(),
        "floating_storage_candidates": fs.where("is_floating_storage").count(),
        "avg_tanker_share": flow.agg(F.round(F.avg("tanker_share"), 3).alias("s")).collect()[0]["s"] or 0.0,
        "flow": rows(
            flow.orderBy(F.desc("hour")).select(
                F.date_format("hour", "MM-dd HH:00").alias("hour"), "vessels", "tankers", "tanker_share"
            ),
            12,
        ),
        "floating_storage_top": rows(
            fs.orderBy(F.desc("is_floating_storage"), F.asc("spread_nm")).select(
                "mmsi", "flag_country", "span_hours", "spread_nm", "avg_sog", "n_points", "is_floating_storage"
            ),
            10,
        ),
    }
except Exception:
    commodity = {}

# COMMAND ----------
# Vessel categories, live positions, regional activity, anomalies.
# AIS broadcasts a numeric *ship-type* code (not a cargo manifest), so categories are a
# vessel-class proxy for "what's being carried". Counts are DISTINCT vessels.


def category_expr(col):
    t = F.col(col)
    return (
        F.when(t.between(80, 89), "Tanker (oil · gas · chemical)")
        .when(t.between(70, 79), "Cargo (container · bulk)")
        .when(t.between(60, 69), "Passenger")
        .when(t.between(40, 49), "High-speed craft")
        .when(t == 30, "Fishing")
        .when(t.isin(31, 32, 52), "Tug & tow")
        .when(t.isin(33, 34, 35, 50, 51, 53, 54, 55, 56, 57, 58), "Service / port craft")
        .when(t.isin(36, 37), "Pleasure / sailing")
        .otherwise("Unknown / other")
    )


# Latest static record per vessel (name, type, destination).
static_latest = (
    static.withColumn(
        "rn",
        F.row_number().over(
            Window.partitionBy("mmsi").orderBy(
                F.col("event_time").desc_nulls_last(), F.col("silver_processed_ts").desc_nulls_last()
            )
        ),
    )
    .where("rn = 1")
    .select(
        "mmsi", "ship_name", "destination", category_expr("ship_type").alias("category")
    )
)

vessel_categories = rows(
    static_latest.groupBy("category").agg(F.countDistinct("mmsi").alias("count")).orderBy(F.desc("count")),
    12,
)

# Latest position per vessel (for the live map + regional rollups).
pos_latest = (
    pos.withColumn(
        "rn", F.row_number().over(Window.partitionBy("mmsi").orderBy(F.col("event_time").desc_nulls_last()))
    )
    .where("rn = 1")
    .select(
        "mmsi",
        "flag_country",
        "event_time",
        F.round("latitude", 4).alias("lat"),
        F.round("longitude", 4).alias("lon"),
        F.round("sog", 1).alias("sog"),
        F.round("cog", 0).cast("int").alias("cog"),
        "nav_status",
    )
)

pos_enriched = pos_latest.join(static_latest, "mmsi", "left").withColumn(
    "category", F.coalesce("category", F.lit("Unknown / other"))
)

vessel_positions = rows(
    pos_enriched.orderBy(F.desc("event_time")).select(
        "mmsi",
        "lat",
        "lon",
        F.coalesce("sog", F.lit(0.0)).alias("sog"),
        F.coalesce("cog", F.lit(0)).alias("cog"),
        "flag_country",
        "category",
        F.coalesce("ship_name", F.lit("")).alias("name"),
        F.coalesce("destination", F.lit("")).alias("destination"),
    ),
    320,
)

# Regional activity for the four monitored chokepoints.
# (name, lat_min, lat_max, lon_min, lon_max)
REGIONS = [
    ("Singapore & Malacca", 0.4, 2.0, 102.8, 105.4),
    ("Rotterdam & North Sea", 50.6, 52.8, 2.0, 5.2),
    ("Houston & US Gulf", 26.8, 30.4, -96.2, -92.6),
    ("Strait of Hormuz", 24.2, 27.8, 53.6, 58.2),
]


def region_expr():
    e = None
    for name, la0, la1, lo0, lo1 in REGIONS:
        cond = F.col("lat").between(la0, la1) & F.col("lon").between(lo0, lo1)
        e = F.when(cond, name) if e is None else e.when(cond, name)
    return e.otherwise("Open water / transit")


region_rollup = {
    r["region"]: r
    for r in [
        x.asDict()
        for x in pos_enriched.withColumn("region", region_expr())
        .groupBy("region")
        .agg(
            F.countDistinct("mmsi").alias("vessels"),
            F.round(F.avg("sog"), 1).alias("avg_sog"),
            F.sum(F.when(F.col("sog") < 0.5, 1).otherwise(0)).alias("idle"),
            F.sum(F.when(F.col("sog") >= 0.5, 1).otherwise(0)).alias("moving"),
        )
        .collect()
    ]
}
regions = []
for _name, *_bounds in REGIONS:
    s = region_rollup.get(_name)
    if s:
        regions.append(
            {
                "region": _name,
                "vessels": s["vessels"],
                "avg_sog": s["avg_sog"] or 0.0,
                "idle": s["idle"] or 0,
                "moving": s["moving"] or 0,
            }
        )

# Over-speed outliers: implausibly fast for large vessels (likely glitch or fast craft).
overspeed_df = pos_enriched.where("sog > 30 AND sog <= 102.3")
anomalies = {
    "overspeed_count": overspeed_df.count(),
    "overspeed_sample": rows(
        overspeed_df.orderBy(F.desc("sog")).select(
            "mmsi",
            "flag_country",
            "category",
            "sog",
            F.coalesce("ship_name", F.lit("")).alias("name"),
        ),
        8,
    ),
}

# COMMAND ----------

metrics = {
    "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    "commodity": commodity,
    "vessel_categories": vessel_categories,
    "vessel_positions": vessel_positions,
    "regions": regions,
    "anomalies": anomalies,
    "layers": {
        "bronze": bronze_n,
        "silver_positions": pos_n,
        "silver_quarantine": quar_n,
        "silver_ship_static": static_n,
        "gold_dark_events": gold_n,
    },
    "quarantine_rate": round(quar_n / total_pos, 4) if total_pos else 0.0,
    "quarantine_reasons": rows(
        quar.groupBy("reason_code").count().orderBy(F.desc("count")), 10
    ),
    "dark_events": gold.agg(
        F.count("*").alias("count"),
        F.countDistinct("mmsi").alias("vessels"),
        F.round(F.avg("confidence"), 3).alias("avg_confidence"),
        F.round(F.max("gap_minutes"), 1).alias("max_gap_minutes"),
    ).collect()[0].asDict(),
    "top_dark_events": rows(
        gold.select(
            "mmsi", "flag_country",
            F.date_format("dark_start", "yyyy-MM-dd HH:mm").alias("dark_start"),
            F.date_format("dark_end", "yyyy-MM-dd HH:mm").alias("dark_end"),
            "gap_minutes", "confidence",
        ).orderBy(F.desc("confidence"), F.desc("gap_minutes")),
        12,
    ),
    # Count DISTINCT vessels per flag/destination (not position rows) so high-frequency local
    # craft don't dominate — a far more representative international picture.
    "top_flags": rows(
        pos.groupBy("flag_country")
        .agg(F.countDistinct("mmsi").alias("count"))
        .orderBy(F.desc("count")),
        10,
    ),
    "top_destinations": rows(
        static.where("destination IS NOT NULL")
        .groupBy("destination")
        .agg(F.countDistinct("mmsi").alias("count"))
        .orderBy(F.desc("count")),
        10,
    ),
    "freshness": {
        "latest_event_time": iso(pos, "event_time"),
        "latest_bronze_ingest": iso(load("bronze/ais_raw"), "bronze_ingest_ts"),
        "latest_silver_processed": iso(pos, "silver_processed_ts"),
        "latest_gold_computed": iso(gold, "gold_computed_ts"),
    },
}

dbutils.notebook.exit(json.dumps(metrics))
