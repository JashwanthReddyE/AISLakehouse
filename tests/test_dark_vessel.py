from datetime import datetime, timedelta

from gold.dark_vessel import confidence_score, find_dark_events

BASE = datetime(2026, 6, 3, 0, 0, 0)


def _seq(offsets_minutes, lat=1.2, lon=103.8, sog=10.0):
    """Build reports at the given minute offsets from BASE."""
    return [(BASE + timedelta(minutes=m), lat, lon, sog) for m in offsets_minutes]


def test_no_events_when_regular_cadence():
    # Pings every 5 min, threshold 30 min -> no dark events.
    reports = _seq([0, 5, 10, 15, 20])
    assert find_dark_events(reports, "563000001", gap_threshold_s=1800) == []


def test_single_dark_event_detected():
    # 5-min cadence, then a 2-hour gap, then resumes.
    reports = _seq([0, 5, 10, 130, 135])
    events = find_dark_events(reports, "563000001", gap_threshold_s=1800)
    assert len(events) == 1
    ev = events[0]
    assert ev.dark_start == BASE + timedelta(minutes=10)
    assert ev.dark_end == BASE + timedelta(minutes=130)
    assert ev.gap_seconds == 120 * 60
    assert ev.mmsi == "563000001"


def test_multiple_dark_events():
    reports = _seq([0, 5, 70, 75, 200])
    events = find_dark_events(reports, "x", gap_threshold_s=1800)
    assert len(events) == 2  # gap 5->70 (65min) and 75->200 (125min)


def test_last_known_position_carried():
    reports = [
        (BASE, 1.10, 103.50, 12.0),
        (BASE + timedelta(minutes=5), 1.11, 103.51, 11.0),
        (BASE + timedelta(minutes=90), 1.50, 104.00, 9.0),
    ]
    ev = find_dark_events(reports, "x", gap_threshold_s=1800)[0]
    assert (ev.last_latitude, ev.last_longitude, ev.last_sog) == (1.11, 103.51, 11.0)


def test_unsorted_input_is_handled():
    reports = _seq([135, 0, 10, 5, 130])
    events = find_dark_events(reports, "x", gap_threshold_s=1800)
    assert len(events) == 1
    assert events[0].gap_seconds == 120 * 60


def test_fewer_than_two_reports():
    assert find_dark_events(_seq([0]), "x") == []
    assert find_dark_events([], "x") == []


def test_confidence_higher_for_more_anomalous_gap():
    # Vessel normally pings every ~10s; a 1h gap is far more anomalous than a 31-min gap.
    c_big = confidence_score(3600, 10)
    c_small = confidence_score(1860, 1800)
    assert c_big == 1.0
    assert c_small < c_big


def test_confidence_bounds():
    assert confidence_score(0, 100) == 0.0
    assert confidence_score(10**9, 10) == 1.0
