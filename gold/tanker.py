"""Tanker-flow and floating-storage indicators — AIS as a commodity-intelligence proxy.

These are *indicators* that professional commodity desks use as inputs, NOT a price predictor:
  * tanker classification via AIS ship-type codes (80-89 = tankers),
  * floating storage: a tanker that stays within a small radius, at near-zero speed, for a long
    span = likely idle/storing cargo offshore = a classic oversupply (bearish crude) signal.

Honest scope: a single chokepoint and short history make these illustrative, not market-grade.
This module is the unit-tested source of truth; the gold notebook mirrors it in Spark SQL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TANKER_TYPE_MIN = 80
TANKER_TYPE_MAX = 89
NM_PER_DEG_LAT = 60.0  # 1 degree of latitude ~= 60 nautical miles

# Floating-storage defaults.
FS_RADIUS_NM = 2.0
FS_MIN_SPAN_S = 3600.0  # at least 1 hour
FS_MAX_AVG_SOG = 1.0  # near stationary (knots)
FS_MIN_POINTS = 5


def is_tanker(ship_type: int | None) -> bool:
    """AIS ship-type codes 80-89 are tankers."""
    return ship_type is not None and TANKER_TYPE_MIN <= ship_type <= TANKER_TYPE_MAX


def bbox_diagonal_nm(lats: list[float], lons: list[float]) -> float:
    """Spread proxy: diagonal of the lat/lon bounding box in nautical miles.

    Mirrors the Spark notebook (min/max box rather than a per-point haversine) so the
    unit-tested logic matches what runs in the lakehouse.
    """
    if not lats or not lons:
        return 0.0
    center_lat = (max(lats) + min(lats)) / 2.0
    dlat_nm = (max(lats) - min(lats)) * NM_PER_DEG_LAT
    dlon_nm = (max(lons) - min(lons)) * NM_PER_DEG_LAT * math.cos(math.radians(center_lat))
    return math.hypot(dlat_nm, dlon_nm)


@dataclass(frozen=True)
class FloatingStorageResult:
    is_candidate: bool
    spread_nm: float
    span_seconds: float
    avg_sog: float | None
    n_points: int


def classify_floating_storage(
    lats: list[float],
    lons: list[float],
    sogs: list[float | None],
    span_seconds: float,
    *,
    radius_nm: float = FS_RADIUS_NM,
    min_span_s: float = FS_MIN_SPAN_S,
    max_avg_sog: float = FS_MAX_AVG_SOG,
    min_points: int = FS_MIN_POINTS,
) -> FloatingStorageResult:
    """Decide whether a tanker's track looks like floating storage (idle, clustered, slow)."""
    n = len(lats)
    spread = bbox_diagonal_nm(lats, lons)
    valid_sogs = [s for s in sogs if s is not None]
    avg_sog = sum(valid_sogs) / len(valid_sogs) if valid_sogs else None

    candidate = (
        n >= min_points
        and spread <= radius_nm
        and span_seconds >= min_span_s
        and avg_sog is not None
        and avg_sog <= max_avg_sog
    )
    return FloatingStorageResult(
        is_candidate=candidate,
        spread_nm=round(spread, 3),
        span_seconds=span_seconds,
        avg_sog=round(avg_sog, 2) if avg_sog is not None else None,
        n_points=n,
    )
