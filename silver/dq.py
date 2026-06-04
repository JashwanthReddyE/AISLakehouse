"""Data-quality gate for position reports.

Returns a single reason code for the first failing check, or None if the record is clean.
The silver job routes any record with a reason code to a quarantine table (never drops it).
The same checks are mirrored as SQL CASE expressions in the silver notebook.
"""

from __future__ import annotations

from .mmsi import mmsi_mid

# AIS speed-over-ground decodes to knots; 102.3 is the "not available" sentinel. Anything
# above that is physically impossible for a vessel and indicates a corrupt report.
SOG_MAX_KNOTS = 102.3


def position_reason(
    mmsi: str | int | None,
    latitude: float | None,
    longitude: float | None,
    sog: float | None,
    event_time: object | None,
) -> str | None:
    """First failing DQ reason code for a position report, or None if it passes."""
    if mmsi_mid(mmsi) is None:
        return "INVALID_MMSI"
    if not event_time:
        return "BAD_EVENT_TIME"
    if latitude is None or not (-90.0 <= latitude <= 90.0):
        return "BAD_LAT"
    if longitude is None or not (-180.0 <= longitude <= 180.0):
        return "BAD_LON"
    if latitude == 0.0 and longitude == 0.0:
        return "NULL_ISLAND"
    if sog is not None and (sog < 0.0 or sog > SOG_MAX_KNOTS):
        return "IMPLAUSIBLE_SOG"
    return None
