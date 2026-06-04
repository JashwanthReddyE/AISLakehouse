"""Configuration loading and pure parsing helpers for the ingestion service.

Kept dependency-free (no python-dotenv) and side-effect-light so the parsing
logic can be unit-tested without a real environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AIS_WS_URL = "wss://stream.aisstream.io/v0/stream"
DEFAULT_MESSAGE_TYPES = ("PositionReport", "ShipStaticData")

# AISStream bounding box: [[[minLat, minLon], [maxLat, maxLon]]]
BoundingBox = tuple[float, float, float, float]  # (minLat, minLon, maxLat, maxLon)


def parse_bbox(raw: str) -> BoundingBox:
    """Parse a 'minLat,minLon,maxLat,maxLon' string into a validated tuple."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"AIS_BBOX must have 4 comma-separated values, got: {raw!r}")
    try:
        min_lat, min_lon, max_lat, max_lon = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"AIS_BBOX values must be numbers, got: {raw!r}") from exc

    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError(f"AIS_BBOX latitudes must be in [-90, 90]: {raw!r}")
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError(f"AIS_BBOX longitudes must be in [-180, 180]: {raw!r}")
    if min_lat >= max_lat or min_lon >= max_lon:
        raise ValueError(f"AIS_BBOX min must be strictly less than max: {raw!r}")

    return (min_lat, min_lon, max_lat, max_lon)


def bbox_to_aisstream(bbox: BoundingBox) -> list[list[list[float]]]:
    """Convert (minLat, minLon, maxLat, maxLon) to AISStream's nested [[[lat,lon],[lat,lon]]]."""
    min_lat, min_lon, max_lat, max_lon = bbox
    return [[[min_lat, min_lon], [max_lat, max_lon]]]


def load_env_file(path: str | os.PathLike[str] = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no interpolation.

    Does not overwrite variables already present in the real environment.
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    api_key: str
    eventhub_connection_string: str
    eventhub_name: str
    bbox: BoundingBox
    message_types: tuple[str, ...] = DEFAULT_MESSAGE_TYPES
    idle_timeout_s: float = 30.0
    backoff_max_s: float = 60.0

    @classmethod
    def from_env(cls, *, load_dotenv: bool = True) -> Settings:
        if load_dotenv:
            load_env_file()
        try:
            api_key = os.environ["AISSTREAM_API_KEY"]
            conn = os.environ["EVENTHUB_CONNECTION_STRING"]
        except KeyError as exc:
            raise RuntimeError(f"Missing required environment variable: {exc.args[0]}") from exc
        return cls(
            api_key=api_key,
            eventhub_connection_string=conn,
            eventhub_name=os.environ.get("EVENTHUB_NAME", "ais-raw"),
            bbox=parse_bbox(os.environ.get("AIS_BBOX", "1.05,103.5,1.45,104.1")),
            idle_timeout_s=float(os.environ.get("IDLE_TIMEOUT_S", "30")),
            backoff_max_s=float(os.environ.get("BACKOFF_MAX_S", "60")),
        )
