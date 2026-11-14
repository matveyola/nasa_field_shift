from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.modules.imagery import routes, sentinel

GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[-93.42, 41.70], [-93.41, 41.70], [-93.41, 41.71], [-93.42, 41.70]]],
}


def scene(item_id, day, cloud=5, host="sentinel-cogs.s3.us-west-2.amazonaws.com"):
    return {
        "id": item_id,
        "collection": "sentinel-2-l2a",
        "properties": {
            "datetime": f"2025-06-{day:02}T17:12:00Z",
            "eo:cloud_cover": cloud,
            "proj:epsg": 32615,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-94, 41], [-93, 41], [-93, 42], [-94, 42], [-94, 41]]],
        },
        "assets": {"visual": {"href": f"https://{host}/scenes/{item_id}/TCI.tif"}},
    }


def test_field_satellite_selects_latest_usable_scene_before_cutoff(monkeypatch):
    def fake_catalog(url):
        query = parse_qs(urlsplit(url).query)
        assert query["collections"] == ["sentinel-2-l2a"]
        assert query["datetime"] == ["2025-05-27T00:00:00Z/2025-06-30T23:59:59.999Z"]
        assert query["sortby"] == ["-properties.datetime"]
        return [
            scene("clouded", 30, cloud=91),
            scene("unsafe-host", 30, host="not-sentinel.example.org"),
            scene("older", 15),
            scene("valid", 22),
        ]

    monkeypatch.setattr(sentinel, "_fetch_catalog", fake_catalog)
    monkeypatch.setattr(
        routes,
        "require_field",
        lambda _session, field_id: SimpleNamespace(id=field_id, geometry=GEOMETRY),
    )
    sentinel._search.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/api/fields/F-03/satellite?on_or_before=2025-06-30")
    sentinel._search.cache_clear()

    assert response.status_code == 200
    result = response.json()
    assert result["field_id"] == "F-03"
    assert result["id"] == "valid"
    assert result["acquired_at"] == "2025-06-22T17:12:00Z"
    assert result["on_or_before"] == "2025-06-30"
    west, south, east, north = result["bounds"]
    assert west < -93.42 < -93.41 < east and south < 41.70 < 41.71 < north


def test_future_date_is_rejected_before_catalog_lookup():
    with TestClient(create_app()) as client:
        assert client.get("/api/fields/F-03/satellite?on_or_before=2099-01-01").status_code == 422


def test_point_preview_covers_the_click_and_reuses_nearby_catalog_lookup(monkeypatch):
    calls = []

    def fake_catalog(url):
        query = parse_qs(urlsplit(url).query)
        calls.append(query)
        west, south, east, north = map(float, query["bbox"][0].split(","))
        assert west < -93.4158 < -93.4149 < east
        assert south < 41.7041 < 41.7042 < north
        assert east - west < 0.04 and north - south < 0.03
        return [scene("nearby", 22)]

    monkeypatch.setattr(sentinel, "_fetch_catalog", fake_catalog)
    monkeypatch.setattr(sentinel, "time", lambda: 1000)
    monkeypatch.setattr(
        routes, "datetime", SimpleNamespace(now=lambda _: datetime(2025, 6, 30, tzinfo=UTC))
    )
    sentinel._search.cache_clear()
    try:
        with TestClient(create_app()) as client:
            first = client.get("/api/fields/satellite?longitude=-93.4158&latitude=41.7041")
            second = client.get("/api/fields/satellite?longitude=-93.4152&latitude=41.7042")
            next_cell = client.get("/api/fields/satellite?longitude=-93.4149&latitude=41.7042")
    finally:
        sentinel._search.cache_clear()

    assert first.status_code == second.status_code == next_cell.status_code == 200
    assert first.json() == second.json()
    assert first.json()["bounds"] != next_cell.json()["bounds"]
    assert first.json()["id"] == "nearby"
    assert "field_id" not in first.json()
    assert len(calls) == 2


def test_point_preview_rejects_out_of_coverage_coordinates_before_catalog(monkeypatch):
    def unexpected_catalog(*_args):
        raise AssertionError("Invalid coordinates should not reach the catalog.")

    monkeypatch.setattr(sentinel, "_fetch_catalog", unexpected_catalog)
    with TestClient(create_app()) as client:
        for query in (
            "longitude=190&latitude=30",
            "longitude=0&latitude=-81",
            "longitude=NaN&latitude=50",
            "longitude=0&latitude=Infinity",
        ):
            assert client.get(f"/api/fields/satellite?{query}").status_code == 422
