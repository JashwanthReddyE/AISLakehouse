import pytest

from ingestion.config import Settings, bbox_to_aisstream, parse_bbox


def test_parse_bbox_valid():
    assert parse_bbox("1.05,103.5,1.45,104.1") == (1.05, 103.5, 1.45, 104.1)


@pytest.mark.parametrize(
    "raw",
    [
        "1,2,3",  # too few
        "1,2,3,4,5",  # too many
        "a,2,3,4",  # non-numeric
        "95,2,96,4",  # lat out of range
        "1,200,2,201",  # lon out of range
        "5,2,1,4",  # min lat >= max lat
        "1,10,2,5",  # min lon >= max lon
    ],
)
def test_parse_bbox_invalid(raw):
    with pytest.raises(ValueError):
        parse_bbox(raw)


def test_bbox_to_aisstream_shape():
    assert bbox_to_aisstream((1.0, 103.0, 2.0, 104.0)) == [[[1.0, 103.0], [2.0, 104.0]]]


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AISSTREAM_API_KEY", "k")
    monkeypatch.setenv("EVENTHUB_CONNECTION_STRING", "Endpoint=sb://x")
    monkeypatch.setenv("AIS_BBOX", "1.0,103.0,2.0,104.0")
    monkeypatch.setenv("EVENTHUB_NAME", "ais-raw")
    s = Settings.from_env(load_dotenv=False)
    assert s.api_key == "k"
    assert s.bbox == (1.0, 103.0, 2.0, 104.0)
    assert s.eventhub_name == "ais-raw"


def test_settings_missing_required(monkeypatch):
    monkeypatch.delenv("AISSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("EVENTHUB_CONNECTION_STRING", raising=False)
    with pytest.raises(RuntimeError):
        Settings.from_env(load_dotenv=False)
