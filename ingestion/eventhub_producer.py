"""Event Hubs producer: buffers raw AIS frames and publishes them in batches.

Design notes:
  * Body is the **raw AISStream frame, unchanged** — bronze must stay an immutable audit anchor.
    The edge ingest timestamp travels as an Event Hubs application property (`ingest_ts`).
  * Frames are buffered and flushed on a count threshold or a short time bound for throughput.
  * On flush, events are grouped by **MMSI partition key** so a vessel's reports land on one
    partition. (Per-vessel ordering is a nice-to-have, not a correctness requirement: Week-2
    dedup is watermarked and order-insensitive. Grouping keeps the partition-key story honest
    without forcing per-event sends.)
  * Delivery is at-least-once; exactly-once is enforced at the bronze Delta write.

`handle(raw, ingest_ts)` matches the consumer's FrameHandler signature, so the consumer can
push frames straight in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from azure.eventhub import EventData
from azure.eventhub.aio import EventHubProducerClient

logger = logging.getLogger(__name__)

_NO_KEY = "__no_mmsi__"


def extract_mmsi(raw: str) -> str | None:
    """Pull MetaData.MMSI from a raw AISStream frame; None if absent/unparseable."""
    try:
        frame = json.loads(raw)
        mmsi = frame.get("MetaData", {}).get("MMSI")
    except (json.JSONDecodeError, AttributeError):
        return None
    return str(mmsi) if mmsi is not None else None


class EventHubFrameProducer:
    def __init__(
        self,
        client: EventHubProducerClient,
        *,
        max_buffer: int = 200,
        max_wait_s: float = 2.0,
    ) -> None:
        self._client = client
        self._max_buffer = max_buffer
        self._max_wait_s = max_wait_s
        self._buffer: list[tuple[str, str]] = []  # (raw, ingest_ts)
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    @classmethod
    def from_connection_string(
        cls, connection_string: str, eventhub_name: str, **kwargs
    ) -> EventHubFrameProducer:
        client = EventHubProducerClient.from_connection_string(
            connection_string, eventhub_name=eventhub_name
        )
        return cls(client, **kwargs)

    async def start(self) -> None:
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def handle(self, raw: str, ingest_ts: str) -> None:
        async with self._lock:
            self._buffer.append((raw, ingest_ts))
            full = len(self._buffer) >= self._max_buffer
        if full:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            pending, self._buffer = self._buffer, []

        # Group by MMSI partition key so a vessel's frames land on one partition.
        groups: dict[str, list[EventData]] = defaultdict(list)
        for raw, ingest_ts in pending:
            event = EventData(raw)
            event.properties = {"ingest_ts": ingest_ts}
            groups[extract_mmsi(raw) or _NO_KEY].append(event)

        for key, events in groups.items():
            partition_key = None if key == _NO_KEY else key
            await self._client.send_batch(events, partition_key=partition_key)
        logger.debug("Flushed %d frames in %d partition groups", len(pending), len(groups))

    async def _flush_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._max_wait_s)
                await self.flush()
        except asyncio.CancelledError:
            await self.flush()  # final drain
            raise

    async def close(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()
        await self._client.close()
