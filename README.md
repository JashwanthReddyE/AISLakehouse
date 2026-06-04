# AISLakehouse — Real-Time Maritime Behavior Lakehouse

A streaming data-engineering project that ingests a live global ship-position feed
([AISStream.io](https://aisstream.io)) over a persistent WebSocket, buffers it through Azure
Event Hubs, and builds a Medallion lakehouse on Databricks + ADLS Gen2 — with the headline being
**vessel-behavior analytics** (dark-vessel detection, loitering, port-call inference) rather than
"dots on a map."

> **Status: Week 4 — 24/7 ingestion live + results dashboard.** Full pipeline running:
> `AISStream → Container Apps consumer (24/7) → Event Hubs → bronze → silver → gold → dashboard`.
>
> ### ▶ Live dashboard: **https://aislakehouse.vercel.app**
> Watching Singapore · Rotterdam · Houston · Strait of Hormuz in real time.

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
| `silver/` | Pure-Python (unit-tested) silver logic: MMSI→flag, destination normalization, DQ |
| `gold/` | Pure-Python (unit-tested) analytics: dark-vessel detection + tanker/floating-storage |
| `databricks/bronze_stream.py` | Structured Streaming bronze write |
| `databricks/silver_stream.py` | bronze→silver: parse, watermark+dedup, enrich, DQ quarantine |
| `databricks/gold_dark_vessel.py` | silver→gold: per-vessel reporting-gap detection → `dark_events` |
| `databricks/gold_tanker_flow.py` | silver→gold: tanker classification, flow, floating-storage indicator |
| `databricks/export_metrics.py` | exports all-layer metrics → JSON for the dashboard |
| `serving/` | static results dashboard (`index.html`) + generator (`build_dashboard.py`) |
| `infra/` | Bicep IaC: base (`main.bicep`) + 24/7 ingestion (`ingestion.bicep` → Container Apps) |
| `tests/` | Unit tests (no network): config, backoff/heartbeat, producer, MMSI, destinations, DQ |
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
# Base: budget alert FIRST, then Event Hubs (Standard), ADLS Gen2, Key Vault.
./infra/deploy.ps1
# Always-on ingestion: ACR + cloud image build + Container Apps (24/7 consumer).
./infra/deploy_ingestion.ps1
```

## Results dashboard

A self-contained `serving/index.html` summarises every layer (row counts, quarantine rate,
dark-vessel events, flag states, normalized destinations, freshness). Regenerate after a
pipeline run:

```bash
# 1. run databricks/export_metrics.py -> save its JSON output to serving/metrics.json
# 2. render the page:
python serving/build_dashboard.py
# 3. open serving/index.html  (or host serving/ on GitHub Pages / Vercel)
```

## Cost discipline (non-negotiable)

- **Budget + alert is created before any billable resource.**
- Main cost driver is **Event Hubs Standard tier** (required for the Kafka endpoint, ~$11/mo if left
  running). Run the consumer only while iterating and **tear down the resource group between
  sessions** (`az group delete -n <rg>`). Target spend ≈ $0.

## Roadmap

- **Week 2 — Silver:** ✅ done. Parses `PositionReport`/`ShipStaticData`, watermarked dedup on
  `(MMSI, event_time)`, MMSI→flag enrichment, destination normalization, DQ quarantine (rate ~1.9%).
- **Week 3 — Gold:** ✅ dark-vessel detection working end-to-end (gap analysis + confidence,
  unit-tested). Remaining: optional dbt formalization + minimal serving query.
- **Week 4 — Maturity:** ✅ 24/7 ingestion on Container Apps + static results dashboard.
  Remaining: scheduled gold refresh + LLM daily brief, deeper observability.

> **Dark-vessel caveat:** terrestrial AIS has genuine coverage dead zones, and sparse ingestion
> creates gaps too — so a dark event is a *candidate* signal, not proof of intent. Continuous
> ingestion (Week 4) is what turns these from coverage artifacts into real behavior signal.

### Commodity indicators (not price prediction)

A second gold metric derives **tanker flow** (hourly distinct-tanker transit through the strait)
and a **floating-storage** proxy (tankers clustered within a small radius at ~0 speed for a long
span — a classic crude-oversupply signal). These are *indicators* that commodity desks use as
inputs, **not** an oil/gas price predictor: a single chokepoint and short history make them
illustrative, not market-grade. Credible alpha would need price series, multi-region coverage,
long history, and a backtested model.
