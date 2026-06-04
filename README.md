# AISLakehouse — Real-Time Maritime Behavior Lakehouse

A streaming data-engineering project that ingests a live global ship-position feed
([AISStream.io](https://aisstream.io)) over a persistent WebSocket, buffers it through Azure
Event Hubs, and builds a Medallion lakehouse on Databricks + ADLS Gen2 — with the headline being
**vessel-behavior analytics** (dark-vessel detection, loitering, port-call inference) rather than
"dots on a map."

> **Status: Week 1** — ingestion slice proven end-to-end:
> `AISStream → local Python consumer → Event Hubs → Databricks Structured Streaming → bronze Delta`.

## Architecture (Week 1 slice)

```
        AISStream.io (WebSocket push feed)
                    │
                    ▼
   ingestion/  ── always-on Python consumer
   - WebSocket lifecycle: reconnect w/ backoff, idle heartbeat, graceful shutdown
   - attaches ingest_ts, publishes raw JSON
                    │
                    ▼
   Azure Event Hubs (Kafka-compatible, Standard tier)
   - durable buffer, replay, at-least-once delivery
                    │
                    ▼
   databricks/bronze_stream.py — Spark Structured Streaming
   - reads Event Hubs via built-in Kafka connector
   - raw append → bronze Delta on ADLS Gen2
   - exactly-once via checkpointing
```

## Streaming hard-parts (the "senior" signals)

- **WebSocket lifecycle:** exponential backoff + jitter on reconnect, idle/heartbeat timeout,
  graceful SIGINT/SIGTERM shutdown, resumption without data loss.
- **Exactly-once into bronze** via Spark checkpointing + Delta transactional writes. The producer
  is only at-least-once; dedup/idempotency is enforced downstream.
- **Decoupling** ingest rate from processing via the Event Hubs durable log (replayable).
- Bronze stays **raw and immutable** — no parsing — as the replay/audit anchor.

## Layout

| Path | Purpose |
|---|---|
| `ingestion/` | Always-on WebSocket consumer + Event Hubs producer |
| `databricks/bronze_stream.py` | Structured Streaming bronze write |
| `infra/` | Bicep IaC (budget alert first, Event Hubs, ADLS Gen2, Key Vault) |
| `tests/` | Unit tests (no network): config, backoff/heartbeat, producer batching |
| `.github/workflows/ci.yml` | ruff + pytest on PR |

## Quickstart (local ingestion)

```bash
python -m venv .venv && .venv/Scripts/activate    # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env        # fill in AISSTREAM_API_KEY + EVENTHUB_CONNECTION_STRING
python -m ingestion.main
```

## Provision Azure (IaC)

```powershell
# Sets up budget alert FIRST, then Event Hubs (Standard), ADLS Gen2, Key Vault.
./infra/deploy.ps1
```

## Cost discipline (non-negotiable)

- **Budget + alert is created before any billable resource.**
- Main cost driver is **Event Hubs Standard tier** (required for the Kafka endpoint, ~$11/mo if left
  running). Run the consumer only while iterating and **tear down the resource group between
  sessions** (`az group delete -n <rg>`). Target spend ≈ $0.

## Roadmap

- **Week 2 — Silver:** parse `PositionReport`/`ShipStaticData`, watermarked dedup on
  `(MMSI, event_time)`, MMSI→flag enrichment, destination normalization, DQ quarantine.
- **Week 3 — Gold:** dark-vessel detection (primary), dbt models + tests, minimal serving.
- **Week 4 — Maturity:** Container Apps deploy, CI/CD, scheduled gold refresh + LLM daily brief,
  observability, teardown script.
