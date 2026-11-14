"""Sentinel-2 indices from surface reflectance, never a soil-water measurement.

Formulas are explicit B08 variants on the existing 20 m SCL analysis grid.
Reference: https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/
"""

import numpy as np

from field_shift.modules.imagery.vegetation_schemas import SpectralSummary


def spectral_pixels(red, nir, valid, bands: dict[str, np.ndarray]):
    if any(value.shape != red.shape for value in [nir, valid, *bands.values()]):
        raise ValueError("Spectral bands must share one analysis grid.")
    output = {}

    def calculate(name, required, numerator, denominator):
        mask = valid.copy()
        for band in required:
            mask &= np.isfinite(band) & (band > 0) & (band <= 1.5)
        mask &= np.isfinite(denominator) & (np.abs(denominator) > 0.001)
        values = np.full(red.shape, np.nan, dtype=np.float64)
        np.divide(numerator, denominator, out=values, where=mask)
        values[~np.isfinite(values)] = np.nan
        output[name] = values

    if (edge := bands.get("rededge1")) is not None:
        calculate("ndre", [nir, edge], nir - edge, nir + edge)
        calculate("reci", [nir, edge], nir - edge, edge)
        if (green := bands.get("green")) is not None:
            calculate(
                "mcari", [red, edge, green], ((edge - red) - 0.2 * (edge - green)) * edge, red
            )
    if (swir := bands.get("swir16")) is not None:
        calculate("ndmi", [nir, swir], nir - swir, nir + swir)
    if (green := bands.get("green")) is not None:
        calculate("ndwi", [nir, green], green - nir, green + nir)
    return output


def summaries(indices, inside_count: int):
    result = {}
    for name, values in indices.items():
        observed = values[np.isfinite(values)]
        if observed.size < 10:
            continue
        p10, p90 = np.percentile(observed, (10, 90))
        result[name] = SpectralSummary(
            mean=round(float(np.mean(observed)), 4),
            p10=round(float(p10), 4),
            p90=round(float(p90), 4),
            pixel_count=observed.size,
            valid_percent=round(100 * observed.size / inside_count, 2),
        )
    return result
