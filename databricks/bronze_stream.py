# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze stream — Event Hubs (Kafka) -> raw Delta on ADLS Gen2
# MAGIC
# MAGIC Reads the AIS raw feed from Azure Event Hubs via the **built-in Spark Kafka connector**
# MAGIC (no extra library) and appends it, unparsed, to the bronze Delta table on **ADLS Gen2**.
# MAGIC
# MAGIC * **Exactly-once** into bronze via `checkpointLocation` + Delta transactional writes.
# MAGIC * Bronze stays **raw/immutable**: the message body is stored verbatim alongside Kafka
# MAGIC   metadata (`topic/partition/offset/timestamp`) — the replay & audit anchor.
# MAGIC * ADLS auth is handled by a **Unity Catalog external location** backed by a service
# MAGIC   principal storage credential (serverless-compatible; no Spark account-key config).
# MAGIC * Parsing, dedup, and enrichment are deliberately deferred to silver (Week 2).

# COMMAND ----------

dbutils.widgets.text("eh_namespace_fqdn", "", "Event Hubs namespace FQDN")
dbutils.widgets.text("eh_name", "ais-raw", "Event hub (topic) name")
dbutils.widgets.text("storage_account", "", "ADLS Gen2 storage account name")
dbutils.widgets.text("lake_container", "lakehouse", "Lake container")
dbutils.widgets.text("secret_scope", "aislakehouse", "Databricks secret scope")

EH_NS = dbutils.widgets.get("eh_namespace_fqdn")  # e.g. aislake-ehns-xxxx.servicebus.windows.net
EH_NAME = dbutils.widgets.get("eh_name")
STORAGE = dbutils.widgets.get("storage_account")
CONTAINER = dbutils.widgets.get("lake_container")
SCOPE = dbutils.widgets.get("secret_scope")

# Event Hubs listen connection string lives in a Databricks secret scope.
EH_CONN = dbutils.secrets.get(SCOPE, "eventhub-listen-connection-string")

BRONZE_PATH = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net/bronze/ais_raw"
CHECKPOINT_PATH = f"abfss://{CONTAINER}@{STORAGE}.dfs.core.windows.net/_checkpoints/bronze_ais_raw"

# Event Hubs Kafka endpoint: SASL_SSL on 9093, username is the literal "$ConnectionString".
# NOTE: passed as a Kafka read option (allowed on serverless), not a Spark conf.
EH_JAAS = (
    'kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule required '
    f'username="$ConnectionString" password="{EH_CONN}";'
)

# COMMAND ----------

import json

from pyspark.sql import functions as F

raw = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", f"{EH_NS}:9093")
    .option("kafka.security.protocol", "SASL_SSL")
    .option("kafka.sasl.mechanism", "PLAIN")
    .option("kafka.sasl.jaas.config", EH_JAAS)
    .option("subscribe", EH_NAME)
    .option("startingOffsets", "earliest")
    .option("failOnDataLoss", "false")  # Event Hubs retention is short (1 day) in Week 1
    .option("includeHeaders", "true")
    .load()
)

bronze = raw.select(
    F.col("value").cast("string").alias("raw_payload"),
    F.col("topic"),
    F.col("partition").alias("kafka_partition"),
    F.col("offset").alias("kafka_offset"),
    F.col("timestamp").alias("kafka_timestamp"),
    # Edge ingest_ts the consumer set as an Event Hubs property (if present).
    F.expr(
        "try_element_at(filter(headers, h -> h.key = 'ingest_ts'), 1).value"
    ).cast("string").alias("edge_ingest_ts"),
    F.current_timestamp().alias("bronze_ingest_ts"),
)

# COMMAND ----------

query = (
    bronze.writeStream.format("delta")
    .outputMode("append")
    .option("checkpointLocation", CHECKPOINT_PATH)
    # availableNow drains what's buffered then stops — clean for iterating + the exactly-once
    # demo (re-run: checkpoint resumes, no duplicate offsets). Swap to a processingTime trigger
    # for a continuously-running stream.
    .trigger(availableNow=True)
    .start(BRONZE_PATH)
)
query.awaitTermination()

# COMMAND ----------

# Verify + emit a machine-readable summary (also visible when run as a job).
df = spark.read.format("delta").load(BRONZE_PATH)
total = df.count()
dup_groups = df.groupBy("kafka_partition", "kafka_offset").count().filter("count > 1").count()

print(f"bronze rows: {total}")
print(f"duplicate (partition, offset) groups (must be 0): {dup_groups}")
display(df.orderBy(F.desc("bronze_ingest_ts")).limit(10))

dbutils.notebook.exit(
    json.dumps({"bronze_path": BRONZE_PATH, "rows": total, "duplicate_offset_groups": dup_groups})
)
