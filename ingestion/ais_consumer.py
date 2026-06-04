"""AISStream WebSocket consumer with reconnect/backoff, idle heartbeat, and graceful shutdown.

The hard-parts that signal real streaming engineering live here:
  * subscription must be sent within 3s of connecting (AISStream closes the socket otherwise)
  * reconnect on close/error/idle-timeout with exponential backoff + jitter
  * an idle (heartbeat) timeout so a silently-dead socket is detected and recycled
  * graceful shutdown via an asyncio.Event so in-flight frames are flushed

Frame durability and exactly-once are NOT this layer's job — they are enforced by Event Hubs
(at-least-once) and the Spark/Delta bronze write (exactly-once via checkpoints).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import websockets

from .config import AIS_WS_URL, BoundingBox, Settings, bbox_to_aisstream

logger = logging.getLogger(__name__)

# Handler receives the raw frame text and the ingest timestamp (ISO-8601 UTC).
FrameHandler = Callable[[str, str], Awaitable[None]]


def build_subscription(
    api_key: str, bbox: BoundingBox, message_types: tuple[str, ...]
) -> dict:
    """Build the AISStream subscription message (sent immediately after connect)."""
    return {
        "APIKey": api_key,
        "BoundingBoxes": bbox_to_aisstream(bbox),
        "FilterMessageTypes": list(message_types),
    }


def compute_backoff(attempt: int, *, base: float = 1.0, cap: float = 60.0) -> float:
    """Deterministic capped exponential backoff (pre-jitter). attempt starts at 1."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return min(cap, base * (2 ** (attempt - 1)))


def apply_jitter(delay: float, rng: random.Random | None = None) -> float:
    """Full jitter: uniform in [0, delay]. Avoids thundering-herd reconnect storms."""
    rng = rng or random
    return rng.uniform(0, delay)


class AISConsumer:
    """Owns the WebSocket lifecycle and pumps raw frames into a handler."""

    def __init__(
        self,
        settings: Settings,
        *,
        connect=websockets.connect,
        stable_run_s: float = 30.0,
    ) -> None:
        self._settings = settings
        self._connect = connect
        # A connection that survives this long is considered "stable" -> reset backoff.
        self._stable_run_s = stable_run_s
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self, handler: FrameHandler) -> None:
        """Connect/consume loop. Returns only after shutdown is requested."""
        attempt = 0
        while not self._shutdown.is_set():
            attempt += 1
            connected_at = None
            try:
                connected_at = await self._run_once(handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any connection-level error
                logger.warning("AIS connection error (attempt %d): %s", attempt, exc)

            if self._shutdown.is_set():
                break

            # Reset backoff if the last connection stayed up long enough to be healthy.
            if connected_at is not None and (
                asyncio.get_event_loop().time() - connected_at >= self._stable_run_s
            ):
                attempt = 0
                continue

            delay = apply_jitter(compute_backoff(attempt, cap=self._settings.backoff_max_s))
            logger.info("Reconnecting in %.1fs (attempt %d)", delay, attempt)
            await self._sleep_or_shutdown(delay)

        logger.info("AIS consumer shut down cleanly.")

    async def _run_once(self, handler: FrameHandler) -> float:
        """One connection lifetime. Returns the loop-time at which it connected."""
        sub = build_subscription(
            self._settings.api_key, self._settings.bbox, self._settings.message_types
        )
        async with self._connect(AIS_WS_URL) as ws:
            connected_at = asyncio.get_event_loop().time()
            # Subscription MUST be sent within 3s or AISStream drops the connection.
            await ws.send(json.dumps(sub))
            logger.info("Subscribed to AISStream bbox=%s", self._settings.bbox)

            while not self._shutdown.is_set():
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=self._settings.idle_timeout_s
                    )
                except TimeoutError:
                    # Idle past the heartbeat window: treat the socket as stale and recycle.
                    logger.warning(
                        "No frame in %.0fs; recycling connection.",
                        self._settings.idle_timeout_s,
                    )
                    return connected_at
                ingest_ts = datetime.now(UTC).isoformat()
                await handler(raw if isinstance(raw, str) else raw.decode("utf-8"), ingest_ts)
            return connected_at

    async def _sleep_or_shutdown(self, delay: float) -> None:
        """Sleep, but wake early if shutdown is requested."""
        try:
            await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
        except TimeoutError:
            pass
