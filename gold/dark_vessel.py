"""Dark-vessel detection: find AIS reporting gaps where a vessel goes silent then reappears.

A "dark event" is a gap between two consecutive position reports for the same vessel that
exceeds a threshold. We score confidence by how anomalous the gap is relative to that vessel's
own typical reporting cadence — a 35-min gap for a vessel that normally pings every 30 min is
barely notable; a 6-hour gap for one that pings every 10s is highly anomalous.

CAVEAT (modeled, not a bug): terrestrial AIS has genuine coverage dead zones, and sparse
ingestion produces gaps too. A dark event is a *candidate* signal, not proof of intent.

This module is the unit-tested source of truth; the gold notebook mirrors it with Spark windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Defaults (seconds). A gap beyond the threshold while otherwise in coverage is a candidate.
DEFAULT_GAP_THRESHOLD_S = 1800.0  # 30 minutes
DEFAULT_NORMAL_MULTIPLIER = 4.0  # gap this many x the vessel's normal cadence -> confidence 1.0


@dataclass(frozen=True)
class DarkEvent:
    mmsi: str
    dark_start: datetime  # last report before going silent
    dark_end: datetime  # first report after reappearing
    gap_seconds: float
    last_latitude: float | None
    last_longitude: float | None
    last_sog: float | None
    normal_gap_seconds: float
    confidence: float


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def confidence_score(
    gap_seconds: float, normal_gap_seconds: float, multiplier: float = DEFAULT_NORMAL_MULTIPLIER
) -> float:
    """0..1: how anomalous this gap is vs the vessel's normal cadence."""
    denom = multiplier * max(normal_gap_seconds, 1.0)
    return max(0.0, min(1.0, gap_seconds / denom))


def find_dark_events(
    reports: list[tuple[datetime, float | None, float | None, float | None]],
    mmsi: str,
    *,
    gap_threshold_s: float = DEFAULT_GAP_THRESHOLD_S,
    multiplier: float = DEFAULT_NORMAL_MULTIPLIER,
) -> list[DarkEvent]:
    """Detect dark events for one vessel.

    reports: list of (event_time, latitude, longitude, sog). Order-independent (sorted here).
    """
    if len(reports) < 2:
        return []
    ordered = sorted(reports, key=lambda r: r[0])
    times = [r[0] for r in ordered]
    gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]

    # "Normal" cadence = median of the non-dark gaps (fallback to the threshold itself).
    normal_candidates = [g for g in gaps if g <= gap_threshold_s]
    normal = _median(normal_candidates) or gap_threshold_s

    events: list[DarkEvent] = []
    for i, gap in enumerate(gaps, start=1):
        if gap > gap_threshold_s:
            prev = ordered[i - 1]
            events.append(
                DarkEvent(
                    mmsi=mmsi,
                    dark_start=times[i - 1],
                    dark_end=times[i],
                    gap_seconds=gap,
                    last_latitude=prev[1],
                    last_longitude=prev[2],
                    last_sog=prev[3],
                    normal_gap_seconds=normal,
                    confidence=round(confidence_score(gap, normal, multiplier), 4),
                )
            )
    return events
