from gold.tanker import bbox_diagonal_nm, classify_floating_storage, is_tanker


def test_is_tanker():
    assert is_tanker(80) is True
    assert is_tanker(89) is True
    assert is_tanker(84) is True  # tanker carrying dangerous goods
    assert is_tanker(70) is False  # cargo
    assert is_tanker(79) is False
    assert is_tanker(90) is False
    assert is_tanker(None) is False


def test_bbox_diagonal_zero_for_single_point():
    assert bbox_diagonal_nm([1.2], [103.8]) == 0.0


def test_bbox_diagonal_latitude_scale():
    # 1 degree of latitude ~ 60 nm.
    d = bbox_diagonal_nm([1.0, 2.0], [103.8, 103.8])
    assert abs(d - 60.0) < 0.01


def test_floating_storage_positive():
    # 6 points within ~0.5 nm, 2-hour span, ~0 speed -> candidate.
    lats = [1.200, 1.201, 1.200, 1.202, 1.201, 1.200]
    lons = [103.800, 103.801, 103.800, 103.799, 103.800, 103.801]
    sogs = [0.1, 0.0, 0.2, 0.1, 0.0, 0.1]
    res = classify_floating_storage(lats, lons, sogs, span_seconds=7200)
    assert res.is_candidate is True
    assert res.spread_nm <= 2.0
    assert res.n_points == 6


def test_floating_storage_rejected_when_moving():
    lats = [1.20, 1.25, 1.30, 1.35, 1.40, 1.45]
    lons = [103.80, 103.85, 103.90, 103.95, 104.00, 104.05]
    sogs = [12.0, 12.0, 12.0, 12.0, 12.0, 12.0]
    assert classify_floating_storage(lats, lons, sogs, span_seconds=7200).is_candidate is False


def test_floating_storage_rejected_short_span():
    lats = [1.200, 1.201, 1.200, 1.202, 1.201, 1.200]
    lons = [103.800, 103.801, 103.800, 103.799, 103.800, 103.801]
    sogs = [0.1, 0.0, 0.2, 0.1, 0.0, 0.1]
    assert classify_floating_storage(lats, lons, sogs, span_seconds=600).is_candidate is False


def test_floating_storage_rejected_too_few_points():
    res = classify_floating_storage([1.2, 1.2], [103.8, 103.8], [0.0, 0.0], 7200)
    assert res.is_candidate is False


def test_floating_storage_handles_missing_sog():
    res = classify_floating_storage(
        [1.20, 1.20, 1.20, 1.20, 1.20], [103.80] * 5, [None] * 5, span_seconds=7200
    )
    assert res.is_candidate is False  # no usable speed -> not asserted as storage
    assert res.avg_sog is None
