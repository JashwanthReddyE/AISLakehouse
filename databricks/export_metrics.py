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


metrics = {
    "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
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
    "top_flags": rows(
        pos.groupBy("flag_country").count().orderBy(F.desc("count")), 10
    ),
    "top_destinations": rows(
        static.where("destination IS NOT NULL")
        .groupBy("destination").count().orderBy(F.desc("count")),
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
