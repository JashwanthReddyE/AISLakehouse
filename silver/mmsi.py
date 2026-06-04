"""Derive flag state / country from an MMSI via its Maritime Identification Digits (MID).

The first 3 digits of a standard ship-station MMSI are the MID, which maps to a flag state.
Only MMSIs whose first digit is 2-7 are ship stations; others (coast stations 00, SAR
aircraft 111, aids-to-navigation 99, craft 98, etc.) are not vessels and return Unknown.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def _mid_table() -> dict[str, str]:
    raw = json.loads((files("silver.reference") / "mid_country.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if k.isdigit()}


def mmsi_mid(mmsi: str | int | None) -> str | None:
    """Return the 3-digit MID for a valid 9-digit ship-station MMSI, else None."""
    if mmsi is None:
        return None
    digits = str(mmsi).strip()
    if not digits.isdigit() or len(digits) != 9:
        return None
    # Ship stations have a leading digit 2-7. Anything else is not a vessel MMSI.
    if digits[0] not in "234567":
        return None
    return digits[:3]


def mid_to_country(mmsi: str | int | None) -> tuple[str | None, str]:
    """Map an MMSI to (mid, country). Country is 'Unknown' if the MID isn't in the table."""
    mid = mmsi_mid(mmsi)
    if mid is None:
        return (None, "Unknown")
    return (mid, _mid_table().get(mid, "Unknown"))
