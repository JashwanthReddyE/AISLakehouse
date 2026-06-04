import pytest

from silver.destinations import clean_destination, normalize_destination


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  dubai  ", "DUBAI"),
        ("AE-DXB", "AE DXB"),
        (">>SGSIN<<", "SGSIN"),
        ("port   klang", "PORT KLANG"),
        (None, ""),
    ],
)
def test_clean_destination(raw, expected):
    assert clean_destination(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DUBAI", "DUBAI"),
        ("AE DXB", "DUBAI"),
        ("AEDXB", "DUBAI"),
        ("DMC DUBAI", "DUBAI"),
        ("jebel ali", "DUBAI"),
        ("SGSIN", "SINGAPORE"),
        ("PSA Singapore", "SINGAPORE"),
        ("MYPKG", "PORT KLANG"),
        ("westport", "PORT KLANG"),
    ],
)
def test_normalize_known_aliases(raw, expected):
    assert normalize_destination(raw) == expected


@pytest.mark.parametrize("raw", ["", "UNKNOWN", "N/A", "for order", None, "NIL"])
def test_normalize_placeholders_to_none(raw):
    assert normalize_destination(raw) is None


def test_normalize_unmapped_returns_cleaned():
    # Unknown port is kept (cleaned), not dropped.
    assert normalize_destination("Some Random Port 7") == "SOME RANDOM PORT 7"
