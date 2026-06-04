"""Ingestion entry point: AISStream WebSocket -> Event Hubs.

Run with: python -m ingestion.main
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .ais_consumer import AISConsumer
from .config import Settings
from .eventhub_producer import EventHubFrameProducer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ingestion")


async def run() -> None:
    settings = Settings.from_env()
    consumer = AISConsumer(settings)
    producer = EventHubFrameProducer.from_connection_string(
        settings.eventhub_connection_string, settings.eventhub_name
    )

    # Graceful shutdown: SIGINT/SIGTERM -> ask the consumer to stop after the current frame.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, consumer.request_shutdown)
        except NotImplementedError:
            # Windows ProactorEventLoop doesn't support add_signal_handler; fall back below.
            signal.signal(sig, lambda *_: consumer.request_shutdown())

    await producer.start()
    try:
        await consumer.run(producer.handle)
    finally:
        logger.info("Draining producer...")
        await producer.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting.")


if __name__ == "__main__":
    main()
