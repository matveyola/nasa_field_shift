"""Synthetic tiny rasters; no live providers or implicit production fixtures."""

import hashlib
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from field_shift.contracts.planning_v1.field import FieldGeometry
from field_shift.modules.fields.planning_profile.providers import (
    ProviderResult,
    check_source,
    missing_provider,
)
from field_shift.modules.fields.planning_profile.soilgrids import (
    RasterLayer,
    SoilGridsAdapter,
    polygon_mean,
)
from field_shift.modules.fields.planning_profile.solar import solar_position


def raster(tmp_path, profile, values, *, prop="sand", unit="g/kg"):
    path = tmp_path / f"{prop}.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        crs="EPSG:6933",
        transform=from_origin(0, 200, 100, 100),
        nodata=-9999,
    ) as dataset:
        dataset.write(np.array(values, dtype="int16"), 1)
    source = profile.sources[0].model_copy(
        update={
            "id": f"soil-{prop}",
            "checksum_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "spatial_method": "area_weighted",
            "native_resolution_m": 100.0,
        }
    )
    from rasterio.warp import transform_geom

    geometry = transform_geom(
        "EPSG:6933",
        "EPSG:4326",
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]],
        },
    )
    geometry["crs"] = "EPSG:4326"
    import json

    polygon = FieldGeometry.model_validate_json(json.dumps(geometry))
    return RasterLayer(prop, 0, 5, unit, path, source), polygon


def test_scaling_and_real_zero(tmp_path, profile):
    layer, polygon = raster(tmp_path, profile, [[0, 400], [400, 800]])
    assert polygon_mean(layer, polygon) == (0.4, 1.0)
    result = SoilGridsAdapter([layer], now=lambda: profile.as_of).soil(polygon, profile.as_of)
    assert result.status == "available"
    assert result.value[0].sand_fraction == 0.4
    assert result.value[0].field_capacity_m3_m3 is None
    assert result.value[0].wilting_point_m3_m3 is None
    assert result.sources[0].spatial_method == "area_weighted"


@pytest.mark.parametrize(
    "prop,unit,value,expected",
    [
        ("soc", "dg/kg", 123, 12.3),
        ("phh2o", "pH*10", 65, 6.5),
        ("clay", "g/kg", 200, 0.2),
    ],
)
def test_mapped_units(tmp_path, profile, prop, unit, value, expected):
    layer, polygon = raster(tmp_path, profile, [[value] * 2] * 2, prop=prop, unit=unit)
    assert polygon_mean(layer, polygon) == (expected, 1.0)


def test_nodata_and_outage_abstain(tmp_path, profile):
    layer, polygon = raster(tmp_path, profile, [[-9999, 400], [400, 400]])
    mean, coverage = polygon_mean(layer, polygon)
    assert mean == pytest.approx(0.4, abs=1e-12)
    assert coverage == 0.75
    result = SoilGridsAdapter([layer], now=lambda: profile.as_of).soil(polygon, profile.as_of)
    assert result.status == "insufficient_data"
    assert result.value is None
    layer.path.unlink()
    assert (
        SoilGridsAdapter([layer], now=lambda: profile.as_of).soil(polygon, profile.as_of).status
        == "error"
    )
    assert (
        SoilGridsAdapter([], now=lambda: profile.as_of).soil(polygon, profile.as_of).value is None
    )
    assert missing_provider("GAEZ").status == "unsupported"


def test_unit_depth_checksum_and_resource_errors(tmp_path, profile):
    from dataclasses import replace

    layer, polygon = raster(tmp_path, profile, [[400] * 2] * 2)
    for changed in [
        replace(layer, unit="percent"),
        replace(layer, bottom_cm=10),
        replace(
            layer,
            source=layer.source.model_copy(
                update={
                    "checksum_sha256": "0" * 64,
                }
            ),
        ),
    ]:
        with pytest.raises(ValueError):
            polygon_mean(changed, polygon)
    with pytest.raises(ValueError, match="execution bound"):
        polygon_mean(layer, polygon, max_pixels=1)
    with pytest.raises(ValueError):
        ProviderResult("available", None, ())


def test_cache_and_issue_time(profile):
    source = profile.sources[0]
    check_source(source, profile.as_of, profile.as_of, timedelta(days=1))
    with pytest.raises(ValueError, match="stale"):
        check_source(source, profile.as_of, profile.as_of + timedelta(days=2), timedelta(days=1))
    with pytest.raises(ValueError, match="unavailable"):
        check_source(source, profile.as_of - timedelta(days=1), profile.as_of, timedelta(days=1))


@pytest.mark.parametrize(
    "latitude,month,expected_zenith",
    [
        (50, 6, 26.57),
        (50, 12, 73.44),
        (-50, 6, 73.44),
        (-50, 12, 26.57),
    ],
)
def test_G15_hemisphere_season_reference(latitude, month, expected_zenith):
    # Solstice noon at Greenwich: independent declination sanity reference ~23.44°.
    result = solar_position(datetime(2027, month, 21, 12, tzinfo=UTC), latitude, 0, 100)
    assert result.zenith_deg == pytest.approx(expected_zenith, abs=0.2)
    assert result.engine_version == "0.16.1"
    assert result.global_horizontal_w_m2 is None
    assert not hasattr(result, "yield_multiplier")


def test_G15_night_zero_and_nodata():
    result = solar_position(datetime(2027, 12, 21, 12, tzinfo=UTC), 89.9, 0, 0)
    assert result.zenith_deg > 90
    assert result.global_horizontal_w_m2 == result.direct_normal_w_m2 == 0
    assert result.diffuse_horizontal_w_m2 == 0
    with pytest.raises(ValueError, match="elevation"):
        solar_position(datetime(2027, 6, 21, 12, tzinfo=UTC), 50, 0, None)
    with pytest.raises(ValueError, match="UTC"):
        solar_position(datetime(2027, 6, 21, 12), 50, 0, 0)


def test_cache_checks_content_reuses_assets_and_bounds_download(tmp_path, monkeypatch):
    import io

    from field_shift.modules.fields.planning_profile import cache

    payload = b"synthetic-raster-asset"
    checksum = hashlib.sha256(payload).hexdigest()
    url = "https://files.isric.org/synthetic-test.tif"
    calls = []

    class Response(io.BytesIO):
        def geturl(self):
            return url

    def fetch(requested, *, timeout):
        calls.append((requested, timeout))
        return Response(payload)

    monkeypatch.setattr(cache, "urlopen", fetch)
    store = cache.RasterCache(tmp_path)
    assert store.acquire(url, checksum, allowed_hosts={"files.isric.org"}).read_bytes() == payload
    store.acquire(url, checksum, allowed_hosts={"files.isric.org"})
    assert len(calls) == 1
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.acquire(url, "0" * 64, allowed_hosts={"files.isric.org"})
    with pytest.raises(ValueError, match="execution bound"):
        cache.RasterCache(tmp_path / "small", max_bytes=1).acquire(
            url, checksum, allowed_hosts={"files.isric.org"}
        )
    assert not list(tmp_path.rglob("*.partial"))
    with pytest.raises(ValueError, match="HTTPS"):
        store.acquire("http://example.org/file", checksum, allowed_hosts={"files.isric.org"})


@pytest.mark.parametrize(
    "kind,threshold", [("wind", 10), ("frost", 0), ("heat", 30), ("heavy_rain", 20)]
)
def test_hazard_missing_is_not_zero(profile, kind, threshold):
    from field_shift.contracts.planning_v1.common import DateWindow
    from field_shift.modules.fields.planning_profile.hazards import threshold_evidence

    window = DateWindow(start=profile.as_of.date(), end=profile.as_of.date())
    value = threshold_evidence(kind, [], window, None, threshold=threshold, rule_version="test-v1")
    assert value.status == "insufficient_data"
    assert value.value is None


def test_actual_zero_rain_hazard(profile):
    from field_shift.contracts.planning_v1.common import DateWindow
    from field_shift.contracts.planning_v1.weather import DailyWeather
    from field_shift.modules.fields.planning_profile.hazards import threshold_evidence

    instant = profile.as_of.date()
    window = DateWindow(start=instant, end=instant)
    day = DailyWeather(
        date=instant,
        source_date=None,
        lead_days=None,
        tmin_c=None,
        tmax_c=None,
        liquid_rain_mm=0.0,
        et0_mm=None,
        shortwave_mj_m2=None,
        wind_m_s=None,
        relative_humidity_fraction=None,
        snow_water_equivalent_mm=None,
        soil_frozen=None,
        quality="partial",
    )
    evidence = threshold_evidence(
        "heavy_rain", [day], window, profile.sources[0], threshold=20, rule_version="synthetic-v1"
    )
    assert evidence.value == 0
    assert evidence.status == "available"
    assert evidence.reasons[0].code == "threshold_not_exceeded"
