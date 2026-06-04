"""Normalize free-text AIS destination strings to canonical port names.

AIS destination is hand-typed and wildly inconsistent (DUBAI / AE DXB / AEDXB / DMC DUBAI).
We clean the text, map known aliases to a canonical port, and treat placeholders as null.
Unmapped-but-clean values are returned as-is (kept, not dropped) so we never lose signal.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files

# Strings that mean "no destination given".
_PLACEHOLDERS = {"", "UNKNOWN", "NA", "N A", "NIL", "NONE", "ORDER", "ORDERS", "FOR ORDER"}
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _alias_table() -> dict[str, str]:
    raw = json.loads(
        (files("silver.reference") / "destination_aliases.json").read_text(encoding="utf-8")
    )
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def clean_destination(raw: str | None) -> str:
    """Uppercase, strip punctuation to spaces, collapse whitespace."""
    if raw is None:
        return ""
    text = _NON_ALNUM.sub(" ", str(raw).upper())
    return _WS.sub(" ", text).strip()


def normalize_destination(raw: str | None) -> str | None:
    """Return the canonical port, None for placeholders, or the cleaned string if unmapped."""
    cleaned = clean_destination(raw)
    if cleaned in _PLACEHOLDERS:
        return None
    return _alias_table().get(cleaned, cleaned)
