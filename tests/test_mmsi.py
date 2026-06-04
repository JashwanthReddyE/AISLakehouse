import pytest

from silver.mmsi import mid_to_country, mmsi_mid


@pytest.mark.parametrize(
    "mmsi,expected",
    [
        (563012345, "563"),  # Singapore ship station
        ("533123456", "533"),  # Malaysia, string input
        (412345678, "412"),  # China
    ],
)
def test_mmsi_mid_valid(mmsi, expected):
    assert mmsi_mid(mmsi) == expected


@pytest.mark.parametrize(
    "mmsi",
    [
        None,
        "",
        "12345",  # too short
        "1234567890",  # too long
        "abc123456",  # non-numeric
        "003660000",  # coast station (leading 0)
        "111234567",  # SAR aircraft (leading 1)
        "993123456",  # aid to navigation (leading 9)
    ],
)
def test_mmsi_mid_invalid(mmsi):
    assert mmsi_mid(mmsi) is None


def test_mid_to_country_known():
    assert mid_to_country(563012345) == ("563", "Singapore")
    assert mid_to_country("636123456") == ("636", "Liberia")


def test_mid_to_country_unknown_mid():
    # Valid ship-station MMSI but MID not in our curated table.
    mid, country = mid_to_country("700123456")
    assert mid == "700"
    assert country == "Unknown"


def test_mid_to_country_invalid_mmsi():
    assert mid_to_country("003660000") == (None, "Unknown")
