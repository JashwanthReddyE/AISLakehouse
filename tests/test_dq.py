from silver.dq import position_reason


def test_clean_record_passes():
    assert position_reason(563012345, 1.26, 103.8, 12.4, "2026-06-03T03:32:00Z") is None


def test_clean_record_null_sog_ok():
    # SOG may legitimately be absent.
    assert position_reason(563012345, 1.26, 103.8, None, "2026-06-03T03:32:00Z") is None


def test_invalid_mmsi():
    assert position_reason("003660000", 1.26, 103.8, 12.4, "t") == "INVALID_MMSI"


def test_missing_event_time():
    assert position_reason(563012345, 1.26, 103.8, 12.4, None) == "BAD_EVENT_TIME"


def test_bad_lat():
    assert position_reason(563012345, 91.0, 103.8, 12.4, "t") == "BAD_LAT"


def test_bad_lon():
    assert position_reason(563012345, 1.26, 200.0, 12.4, "t") == "BAD_LON"


def test_null_island():
    assert position_reason(563012345, 0.0, 0.0, 0.0, "t") == "NULL_ISLAND"


def test_implausible_sog():
    assert position_reason(563012345, 1.26, 103.8, 150.0, "t") == "IMPLAUSIBLE_SOG"


def test_reason_precedence_mmsi_first():
    # Multiple problems -> MMSI checked first.
    assert position_reason(None, 91.0, 200.0, 150.0, None) == "INVALID_MMSI"
