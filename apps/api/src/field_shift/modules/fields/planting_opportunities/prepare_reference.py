"""Offline MIRCA conversion using the API's declared rasterio/numpy dependencies.

No network access. Pass already extracted rainfed NetCDF files and the pinned
original archive. Reads at most 32 rows at once and writes immutable explicit
outputs; never modifies inputs or fills missing months. Runtime imports no GIS
reader through this module.
"""

import argparse
import gzip
import hashlib
import json
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

ARCHIVE_SHA256 = "815e8704acd9c801051e48ca32440d156bd1796dc9febb8db0796cf15b83c0bb"
SOURCE_URL = "https://www.hydroshare.org/resource/e4582ca0042148338bb5e0148b749ed6/"
CROPS = {
    "Barley": "Barley",
    "Maize": "Maize",
    "Rapeseed": "Rapeseed",
    "Soybeans": "Soy",
    "Sunflower": "Sunflower",
    "Wheat1": "Wheat",
    "Wheat2": "Wheat",
}
SOURCE_SHA256 = {
    "Barley": "46789127f1a55966acf698863e2815363cbe6099443937382068ce17b89aa6cf",
    "Maize": "da688ac9edebc887625c4fed014846a5b5689bf3763fe5c234b766f5c3cf4e11",
    "Rapeseed": "95db6af40a743c51e46a605f2955ab1624bbc2fe1b5442f3beb08552c2682257",
    "Soybeans": "315f736f0754f973e40c9008d87ed3dfd4210dd608efb4bd0e641dbd693677c7",
    "Sunflower": "dc354fce0816793a4114d2fcc77a4e7f20c409d7f25ed219b9359cdd6cb3d28c",
    "Wheat1": "b9d465c610dc4ab8ac4325aa7265ac4b5638a261e37093a11e31fa0c8060c2ed",
    "Wheat2": "0829a1d2e09a16e60f49dacb81b3f333cb6baeef3b7d93ea21f93ae2c417293d",
}
VERSION = "mirca-os-v2-2020-rainfed-rle-v1"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def vector(path, name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(f"netcdf:{path}:{name}") as dataset:
            return dataset.read(1).ravel().tolist()


def prepare(directory, archive, output, receipt, verified_at):
    if output.resolve() == receipt.resolve():
        raise ValueError("Artifact and receipt destinations must be distinct")
    if output.exists() or receipt.exists():
        raise ValueError("Refuse to overwrite existing scientific artifacts")
    if archive.stat().st_size > 50_000_000 or sha(archive) != ARCHIVE_SHA256:
        raise ValueError("Original MIRCA archive checksum/bound mismatch")
    verified = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    if verified.utcoffset() != UTC.utcoffset(verified) or verified > datetime.now(UTC):
        raise ValueError("Explicit UTC verification timestamp no later than execution required")
    artifact = {"version": VERSION, "latitude": [], "longitude": [], "calendars": []}
    sources = []
    for native, crop in CROPS.items():
        path = directory / f"{native}.nc"
        if path.stat().st_size > 2_000_000 or sha(path) != SOURCE_SHA256[native]:
            raise ValueError("Source crop file checksum or extraction bound mismatch")
        latitude, longitude = vector(path, "lat"), vector(path, "lon")
        if artifact["latitude"] and (
            latitude != artifact["latitude"] or longitude != artifact["longitude"]
        ):
            raise ValueError("Crop files do not share exact source coordinates")
        artifact["latitude"], artifact["longitude"] = latitude, longitude
        if len(latitude) != 2160 or len(longitude) != 4320:
            raise ValueError("Unexpected source grid shape")
        rows = []
        with (
            rasterio.open(f"netcdf:{path}:Planting_month") as planting,
            rasterio.open(f"netcdf:{path}:Maturity_month") as maturity,
        ):
            if planting.shape != (2160, 4320) or maturity.shape != planting.shape:
                raise ValueError("Month grids differ from native coordinates")
            for offset in range(0, 2160, 32):
                height = min(32, 2160 - offset)
                window = Window(0, offset, 4320, height)
                arrays = [x.read(1, window=window) for x in (planting, maturity)]
                values = []
                for array in arrays:
                    finite = np.isfinite(array)
                    if np.any(finite & ((array < 1) | (array > 12) | (array != np.floor(array)))):
                        raise ValueError("Invalid source month; no rounding or replacement")
                    values.append(np.where(finite, array, 0).astype(np.uint8))
                for sow, harvest in zip(*values, strict=True):
                    changes = (
                        np.flatnonzero((sow[1:] != sow[:-1]) | (harvest[1:] != harvest[:-1])) + 1
                    )
                    edges = [0, *changes.tolist(), 4320]
                    rows.append(
                        [
                            [start, end, int(sow[start]), int(harvest[start])]
                            for start, end in zip(edges, edges[1:])
                            if sow[start] or harvest[start]
                        ]
                    )
        artifact["calendars"].append({"cycle_id": native, "crop_id": crop, "rows": rows})
        sources.append(
            {
                "file": path.name,
                "archive_path": f"NetCDF/Rainfed/{path.name}",
                "bytes": path.stat().st_size,
                "sha256": sha(path),
            }
        )
    raw = json.dumps(artifact, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 32_000_000:
        raise ValueError("Derived artifact exceeds runtime expansion bound")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    manifest = {
        "version": VERSION,
        "format": "row-runs-json-gzip-v1",
        "artifact_sha256": hashlib.sha256(packed).hexdigest(),
        "expanded_bytes": len(raw),
        "bytes": len(packed),
        "reference_year": 2020,
        "system": "rainfed",
        "resolution_arc_minutes": 5,
        "source_url": SOURCE_URL,
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": (
            "Kebede, E.A., Oluoch, K.O., Siebert, S. et al. Scientific Data 12, 208 (2025). "
            "doi:10.1038/s41597-024-04313-w; MIRCA-OS v2, March 2026."
        ),
        "archive_sha256": ARCHIVE_SHA256,
        "archive_bytes": archive.stat().st_size,
        "archive_url": SOURCE_URL + "data/contents/Crop%20Calendar/Gridded%20Crop%20Calendar.rar",
        "available_at": "2026-03-26T00:00:00Z",
        "verified_at": verified_at,
        "availability_basis": (
            "Conservative next UTC day after official v2 resource last update "
            "2026-03-25 14:29 UTC; reference year 2020 is not publication time."
        ),
        "source_files": sources,
        "converter_sha256": sha(Path(__file__)),
        "reader": {
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
            "numpy": np.__version__,
        },
        "limitations": [
            (
                "Administrative-unit monthly reference, not field-observed cultivation or "
                "admissible sowing dates."
            ),
            "No-data retains absent crop presence or reporting; no nearby-cell substitution.",
            "Wheat1 and Wheat2 remain source cycle IDs, not globally winter/spring cultivars.",
            "Peas has no exact source category; Pulses is deliberately excluded.",
            "No yield, weather probability, profitability or field suitability is inferred.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(packed)
    with receipt.open("x") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--verified-at", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.input_directory, args.archive, args.output, args.receipt, args.verified_at)
        )
    )


if __name__ == "__main__":
    main()
