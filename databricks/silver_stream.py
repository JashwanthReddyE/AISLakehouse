# Databricks notebook source
# MAGIC %md
# MAGIC # Silver stream — bronze raw -> clean / conformed / enriched
# MAGIC
# MAGIC Reads the immutable **bronze** Delta and produces three Delta tables on ADLS Gen2:
# MAGIC
# MAGIC * **silver/positions** — parsed `PositionReport`s, typed, event-time **watermarked** and
# MAGIC   **deduped** on `(mmsi, event_time)`, enriched with **flag/country** from the MMSI MID.
# MAGIC * **silver/ship_static** — parsed `ShipStaticData` with **normalized destinations**.
# MAGIC * **silver/quarantine** — records failing DQ checks, tagged with a **reason code**
# MAGIC   (quarantine-not-drop). The quarantine rate is an observability metric.
# MAGIC
# MAGIC Each output is its own streaming query (native Delta writeStream + checkpoint =
# MAGIC exactly-once). The reference maps below mirror the unit-tested `silver/` Python package.

# COMMAND ----------

dbutils.widgets.text("storage_account", "", "ADLS Gen2 storage account name")
dbutils.widgets.text("lake_container", "lakehouse", "Lake container")
dbutils.widgets.text("watermark", "2 hours", "Event-time watermark")

STORAGE = dbutils.widgets.get("storage_account")
CONTAINER = dbutils.widgets.get("lake_container")
WATERMARK = dbutils.widgets.get("watermark")

base = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net"
BRONZE_PATH = f"{base}/bronze/ais_raw"
SILVER_POS = f"{base}/silver/positions"
SILVER_STATIC = f"{base}/silver/ship_static"
QUARANTINE = f"{base}/silver/quarantine"
CKPT = f"{base}/_checkpoints"

# COMMAND ----------
# Reference maps — MIRROR of silver/reference/*.json (the unit-tested source of truth).

MID_COUNTRY = {
    "201": "Albania", "205": "Belgium", "209": "Cyprus", "210": "Cyprus", "211": "Germany",
    "212": "Cyprus", "215": "Malta", "218": "Germany", "219": "Denmark", "220": "Denmark",
    "224": "Spain", "225": "Spain", "227": "France", "228": "France", "229": "Malta",
    "232": "United Kingdom", "233": "United Kingdom", "234": "United Kingdom",
    "235": "United Kingdom", "236": "Gibraltar", "237": "Greece", "238": "Croatia",
    "239": "Greece", "240": "Greece", "241": "Greece", "244": "Netherlands",
    "245": "Netherlands", "246": "Netherlands", "247": "Italy", "248": "Malta", "249": "Malta",
    "256": "Malta", "257": "Norway", "258": "Norway", "259": "Norway", "265": "Sweden",
    "266": "Sweden", "269": "Switzerland", "273": "Russia", "303": "United States",
    "308": "Bahamas", "309": "Bahamas", "311": "Bahamas", "338": "United States",
    "341": "Saint Kitts and Nevis", "351": "Panama", "352": "Panama", "353": "Panama",
    "354": "Panama", "355": "Panama", "356": "Panama", "357": "Panama", "370": "Panama",
    "371": "Panama", "372": "Panama", "373": "Panama", "374": "Panama",
    "375": "Saint Vincent and the Grenadines", "376": "Saint Vincent and the Grenadines",
    "377": "Saint Vincent and the Grenadines", "403": "Saudi Arabia", "412": "China",
    "413": "China", "414": "China", "416": "Taiwan", "419": "India", "422": "Iran",
    "431": "Japan", "432": "Japan", "440": "South Korea", "441": "South Korea",
    "445": "North Korea", "457": "Mongolia", "470": "United Arab Emirates",
    "471": "United Arab Emirates", "477": "Hong Kong", "525": "Indonesia", "533": "Malaysia",
    "538": "Marshall Islands", "548": "Philippines", "563": "Singapore", "564": "Singapore",
    "565": "Singapore", "566": "Singapore", "567": "Thailand", "574": "Vietnam",
    "577": "Brunei", "636": "Liberia", "637": "Liberia", "657": "Nigeria",
    "667": "Sierra Leone", "710": "Brazil", "725": "Chile", "740": "Peru",
}

DEST_ALIAS = {
    "SINGAPORE": "SINGAPORE", "SGSIN": "SINGAPORE", "SIN": "SINGAPORE", "PSA": "SINGAPORE",
    "PSA SINGAPORE": "SINGAPORE", "SINGAPORE PSA": "SINGAPORE", "JURONG": "SINGAPORE",
    "SG SIN": "SINGAPORE", "SGSIN ANCH": "SINGAPORE", "PORT KLANG": "PORT KLANG",
    "PKG": "PORT KLANG", "MYPKG": "PORT KLANG", "PORTKLANG": "PORT KLANG",
    "KLANG": "PORT KLANG", "PELABUHAN KLANG": "PORT KLANG", "WESTPORT": "PORT KLANG",
    "NORTHPORT": "PORT KLANG", "TANJUNG PELEPAS": "TANJUNG PELEPAS", "PTP": "TANJUNG PELEPAS",
    "MYTPP": "TANJUNG PELEPAS", "TG PELEPAS": "TANJUNG PELEPAS", "DUBAI": "DUBAI",
    "AE DXB": "DUBAI", "AEDXB": "DUBAI", "DXB": "DUBAI", "DMC DUBAI": "DUBAI",
    "JEBEL ALI": "DUBAI", "AEJEA": "DUBAI", "ROTTERDAM": "ROTTERDAM", "NLRTM": "ROTTERDAM",
    "RTM": "ROTTERDAM", "SHANGHAI": "SHANGHAI", "CNSHA": "SHANGHAI", "SHA": "SHANGHAI",
    "HONG KONG": "HONG KONG", "HONGKONG": "HONG KONG", "HKHKG": "HONG KONG", "HKG": "HONG KONG",
    "CN HKG": "HONG KONG", "BUSAN": "BUSAN", "KRPUS": "BUSAN", "PUSAN": "BUSAN",
    "TOKYO": "TOKYO", "JPTYO": "TOKYO", "YOKOHAMA": "YOKOHAMA", "JPYOK": "YOKOHAMA",
    "COLOMBO": "COLOMBO", "LKCMB": "COLOMBO", "JAKARTA": "JAKARTA",
    "TANJUNG PRIOK": "JAKARTA", "IDJKT": "JAKARTA", "HAMBURG": "HAMBURG", "DEHAM": "HAMBURG",
    "ANTWERP": "ANTWERP", "BEANR": "ANTWERP", "ANTWERPEN": "ANTWERP",
}
DEST_PLACEHOLDERS = ["", "UNKNOWN", "NA", "N A", "NIL", "NONE", "ORDER", "ORDERS", "FOR ORDER"]

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.functions import broadcast

AIS_SCHEMA = (
    "struct<"
    "MessageType:string,"
    "MetaData:struct<MMSI:long,ShipName:string,latitude:double,longitude:double,time_utc:string>,"
    "Message:struct<"
    "PositionReport:struct<Latitude:double,Longitude:double,Sog:double,Cog:double,"
    "TrueHeading:int,NavigationalStatus:int>,"
    "ShipStaticData:struct<Name:string,Destination:string,Type:int,ImoNumber:long,"
    "MaximumStaticDraught:double>"
    ">>"
)

mid_dim = broadcast(
    spark.createDataFrame(list(MID_COUNTRY.items()), "mid string, flag_country string")
)
alias_dim = broadcast(
    spark.createDataFrame(list(DEST_ALIAS.items()), "dest_clean string, destination string")
)

# Parsed event-time + MID expressions (mirror silver/mmsi.py and the time_utc format).
EVENT_TIME = F.to_timestamp(
    F.regexp_extract(F.col("f.MetaData.time_utc"), r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", 1)
)
MMSI_STR = F.col("f.MetaData.MMSI").cast("string")
MID_EXPR = F.when(
    (F.length(MMSI_STR) == 9) & (F.substring(MMSI_STR, 1, 1).isin("2", "3", "4", "5", "6", "7")),
    F.substring(MMSI_STR, 1, 3),
)


def read_bronze_parsed():
    return (
        spark.readStream.format("delta")
        .load(BRONZE_PATH)
        .select(
            F.from_json("raw_payload", AIS_SCHEMA).alias("f"),
            "kafka_partition",
            "kafka_offset",
            "bronze_ingest_ts",
        )
    )


# COMMAND ----------
# DQ reason code — mirrors silver/dq.py precedence.

def with_reason_code(df):
    # Runs after the select that already projected `mid`; reference that column, not the raw struct.
    return df.withColumn(
        "reason_code",
        F.when(F.col("mid").isNull(), "INVALID_MMSI")
        .when(F.col("event_time").isNull(), "BAD_EVENT_TIME")
        .when(
            F.col("latitude").isNull() | ~F.col("latitude").between(-90, 90), "BAD_LAT"
        )
        .when(
            F.col("longitude").isNull() | ~F.col("longitude").between(-180, 180), "BAD_LON"
        )
        .when((F.col("latitude") == 0) & (F.col("longitude") == 0), "NULL_ISLAND")
        .when(
            F.col("sog").isNotNull() & ((F.col("sog") < 0) | (F.col("sog") > 102.3)),
            "IMPLAUSIBLE_SOG",
        )
        .otherwise(None),
    )


def positions_base():
    p = read_bronze_parsed().where("f.MessageType = 'PositionReport'")
    p = p.select(
        F.col("f.MetaData.MMSI").alias("mmsi"),
        F.col("f.MetaData.ShipName").alias("ship_name"),
        EVENT_TIME.alias("event_time"),
        F.col("f.Message.PositionReport.Latitude").alias("latitude"),
        F.col("f.Message.PositionReport.Longitude").alias("longitude"),
        F.col("f.Message.PositionReport.Sog").alias("sog"),
        F.col("f.Message.PositionReport.Cog").alias("cog"),
        F.col("f.Message.PositionReport.TrueHeading").alias("true_heading"),
        F.col("f.Message.PositionReport.NavigationalStatus").alias("nav_status"),
        MID_EXPR.alias("mid"),
        "kafka_partition",
        "kafka_offset",
        "bronze_ingest_ts",
    )
    return with_reason_code(p)


# COMMAND ----------
# Query 1: clean, deduped, enriched positions.

valid_positions = (
    positions_base()
    .where("reason_code IS NULL")
    .withWatermark("event_time", WATERMARK)
    .dropDuplicatesWithinWatermark(["mmsi", "event_time"])
    .join(mid_dim, "mid", "left")
    .withColumn("flag_country", F.coalesce("flag_country", F.lit("Unknown")))
    .withColumn("silver_processed_ts", F.current_timestamp())
    .drop("reason_code")
)

q_pos = (
    valid_positions.writeStream.format("delta")
    .outputMode("append")
    .option("checkpointLocation", f"{CKPT}/silver_positions")
    .trigger(availableNow=True)
    .start(SILVER_POS)
)

# COMMAND ----------
# Query 2: quarantine (quarantine-not-drop, with reason code).

quarantine = positions_base().where("reason_code IS NOT NULL").select(
    "mmsi", "event_time", "latitude", "longitude", "sog", "reason_code",
    "kafka_partition", "kafka_offset", F.current_timestamp().alias("silver_processed_ts"),
)
q_quar = (
    quarantine.writeStream.format("delta")
    .outputMode("append")
    .option("checkpointLocation", f"{CKPT}/silver_quarantine")
    .trigger(availableNow=True)
    .start(QUARANTINE)
)

# COMMAND ----------
# Query 3: ship static data with normalized destination.

DEST_CLEAN = F.trim(
    F.regexp_replace(
        F.regexp_replace(F.upper(F.col("f.Message.ShipStaticData.Destination")), r"[^A-Z0-9 ]+", " "),
        r"\s+",
        " ",
    )
)

static = (
    read_bronze_parsed()
    .where("f.MessageType = 'ShipStaticData'")
    .select(
        F.col("f.MetaData.MMSI").alias("mmsi"),
        F.coalesce(F.col("f.Message.ShipStaticData.Name"), F.col("f.MetaData.ShipName")).alias("ship_name"),
        F.col("f.Message.ShipStaticData.ImoNumber").alias("imo"),
        F.col("f.Message.ShipStaticData.Type").alias("ship_type"),
        F.col("f.Message.ShipStaticData.Destination").alias("destination_raw"),
        DEST_CLEAN.alias("dest_clean"),
        F.col("f.Message.ShipStaticData.MaximumStaticDraught").alias("max_draught"),
        MID_EXPR.alias("mid"),
        EVENT_TIME.alias("event_time"),
        "bronze_ingest_ts",
    )
    # Placeholders -> NULL destination.
    .withColumn(
        "dest_clean", F.when(F.col("dest_clean").isin(DEST_PLACEHOLDERS), None).otherwise(F.col("dest_clean"))
    )
    .join(alias_dim, "dest_clean", "left")
    .withColumn("destination", F.coalesce("destination", "dest_clean"))
    .join(mid_dim, "mid", "left")
    .withColumn("flag_country", F.coalesce("flag_country", F.lit("Unknown")))
    .withColumn("silver_processed_ts", F.current_timestamp())
    .drop("dest_clean")
)

q_static = (
    static.writeStream.format("delta")
    .outputMode("append")
    .option("checkpointLocation", f"{CKPT}/silver_static")
    .trigger(availableNow=True)
    .start(SILVER_STATIC)
)

# COMMAND ----------

for q in (q_pos, q_quar, q_static):
    q.awaitTermination()

# COMMAND ----------
# Verify + emit a machine-readable summary (visible as the job's notebook output).
import json

pos_n = spark.read.format("delta").load(SILVER_POS).count()
quar_n = spark.read.format("delta").load(QUARANTINE).count()
static_n = spark.read.format("delta").load(SILVER_STATIC).count()
total = pos_n + quar_n
quarantine_rate = round(quar_n / total, 4) if total else 0.0

pos_df = spark.read.format("delta").load(SILVER_POS)
dup = pos_df.groupBy("mmsi", "event_time").count().filter("count > 1").count()

print(f"positions={pos_n}  quarantine={quar_n}  ship_static={static_n}")
print(f"quarantine_rate={quarantine_rate}  duplicate_(mmsi,event_time)_groups={dup}")
display(pos_df.orderBy(F.desc("event_time")).limit(10))

dbutils.notebook.exit(
    json.dumps(
        {
            "positions": pos_n,
            "quarantine": quar_n,
            "ship_static": static_n,
            "quarantine_rate": quarantine_rate,
            "duplicate_groups": dup,
        }
    )
)
