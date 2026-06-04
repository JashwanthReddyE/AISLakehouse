import random

import pytest

from ingestion.ais_consumer import (
    AISConsumer,
    apply_jitter,
    build_subscription,
    compute_backoff,
)
from ingestion.config import Settings


def make_settings(**overrides):
    base = dict(
        api_key="k",
        eventhub_connection_string="Endpoint=sb://x",
        eventhub_name="ais-raw",
        bbox=(1.0, 103.0, 2.0, 104.0),
        idle_timeout_s=0.02,
        backoff_max_s=60.0,
    )
    base.update(overrides)
    return Settings(**base)


def test_build_subscription_shape():
    sub = build_subscription("key", (1.0, 103.0, 2.0, 104.0), ("PositionReport",))
    assert sub["APIKey"] == "key"
    assert sub["BoundingBoxes"] == [[[1.0, 103.0], [2.0, 104.0]]]
    assert sub["FilterMessageTypes"] == ["PositionReport"]


def test_compute_backoff_exponential_and_capped():
    assert compute_backoff(1, base=1.0, cap=60.0) == 1.0
    assert compute_backoff(2, base=1.0, cap=60.0) == 2.0
    assert compute_backoff(4, base=1.0, cap=60.0) == 8.0
    assert compute_backoff(100, base=1.0, cap=60.0) == 60.0


def test_compute_backoff_rejects_zero():
    with pytest.raises(ValueError):
        compute_backoff(0)


def test_apply_jitter_within_bounds():
    rng = random.Random(42)
    for _ in range(100):
        d = apply_jitter(10.0, rng)
        assert 0.0 <= d <= 10.0


class FakeWebsocket:
    """Async-context-manager websocket double. Emits queued frames, then blocks forever."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, msg):
        self.sent.append(msg)

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        # No more frames: block so the idle-timeout path is exercised.
        import asyncio

        await asyncio.Event().wait()


async def test_run_once_emits_frame_and_sends_subscription():
    ws = FakeWebsocket(['{"MessageType":"PositionReport"}'])
    settings = make_settings()
    consumer = AISConsumer(settings, connect=lambda url: ws)

    received = []

    async def handler(raw, ingest_ts):
        received.append((raw, ingest_ts))
        consumer.request_shutdown()  # stop after first frame

    connected_at = await consumer._run_once(handler)

    assert isinstance(connected_at, float)
    assert received[0][0] == '{"MessageType":"PositionReport"}'
    assert received[0][1]  # ingest_ts attached
    assert len(ws.sent) == 1  # subscription was sent exactly once


async def test_run_once_recycles_on_idle_timeout():
    ws = FakeWebsocket([])  # no frames -> recv blocks -> idle timeout trips
    settings = make_settings(idle_timeout_s=0.02)
    consumer = AISConsumer(settings, connect=lambda url: ws)

    received = []

    async def handler(raw, ingest_ts):
        received.append(raw)

    connected_at = await consumer._run_once(handler)
    assert isinstance(connected_at, float)
    assert received == []  # nothing delivered before recycle
