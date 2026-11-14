import numpy as np
import pytest

from field_shift.modules.imagery.spectral import spectral_pixels, summaries


def test_surface_reflectance_formulas_are_separate_from_soil_moisture():
    red = np.full((4, 4), 0.1)
    nir = np.full((4, 4), 0.6)
    extra = {
        "rededge1": np.full((4, 4), 0.3),
        "green": np.full((4, 4), 0.2),
        "swir16": np.full((4, 4), 0.4),
    }
    values = spectral_pixels(red, nir, np.ones((4, 4), bool), extra)
    assert set(values) == {"ndre", "reci", "mcari", "ndmi", "ndwi"}
    expected = {"ndre": 1 / 3, "reci": 1, "mcari": 0.54, "ndmi": 0.2, "ndwi": -0.5}
    for key, value in expected.items():
        assert np.allclose(values[key], value)
    stats = summaries(values, 16)
    assert stats["reci"].mean == 1 and stats["reci"].valid_percent == 100
    assert stats["ndmi"].pixel_count == 16


def test_missing_bands_zero_denominator_and_clouds_remain_unobserved():
    valid = np.ones((5, 5), bool)
    valid[1, 1] = False
    red, nir = np.full((5, 5), 0.1), np.full((5, 5), 0.5)
    edge = np.full((5, 5), 0.25)
    edge[0, 0], edge[0, 1], edge[0, 2] = 0, np.nan, 2
    values = spectral_pixels(red, nir, valid, {"rededge1": edge})
    assert set(values) == {"ndre", "reci"}
    assert np.isnan(values["reci"][0, :3]).all()
    assert np.isnan(values["ndre"][1, 1])
    assert summaries(values, 25)["reci"].valid_percent == 84
    assert summaries({"ndmi": np.full((2, 2), 0.3)}, 4) == {}
    with pytest.raises(ValueError, match="analysis grid"):
        spectral_pixels(red, nir, valid, {"rededge1": np.zeros((2, 2))})
