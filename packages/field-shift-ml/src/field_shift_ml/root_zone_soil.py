"""Snapshot representative SSURGO horizons for offline root-zone experiments."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from math import isfinite, log1p
from pathlib import Path
from urllib.request import Request, urlopen

from field_shift.modules.geo_ml.ssurgo import SDA_ENDPOINT
from field_shift_ml.fetch_observations import write_atomic
from field_shift_ml.fetch_root_zone_observations import RootZoneSite
from field_shift_ml.train_moisture import write_atomically
from field_shift_ml.train_root_zone import load_targets

HORIZONS = (
    "hzdept_r",
    "hzdepb_r",
    "sandtotal_r",
    "claytotal_r",
    "om_r",
    "dbthirdbar_r",
    "awc_r",
    "ksat_r",
)
COLUMNS = ("mukey", "cokey", "slope_r", "drainagecl", *HORIZONS)
SOIL_FEATURES = (
    "sand_pct",
    "clay_pct",
    "organic_pct",
    "bulk_density_g_cm3",
    "available_capacity_mm",
    "log_saturated_conductivity",
    "subsoil_clay_difference_pct",
    "root_profile_coverage",
    "dominant_slope_pct",
    "drainage_rank",
)
DRAINAGE_CLASSES = (
    "Very poorly drained",
    "Poorly drained",
    "Somewhat poorly drained",
    "Moderately well drained",
    "Well drained",
    "Somewhat excessively drained",
    "Excessively drained",
)


def soil_query(latitude: float, longitude: float) -> str:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid SSURGO coordinates")
    horizons = ", ".join(f"h.{column}" for column in HORIZONS)
    return f"""
        WITH representative AS (
            SELECT TOP 1 p.mukey, c.cokey, c.slope_r, c.drainagecl
            FROM SDA_Get_Mukey_from_intersection_with_WktWgs84(
                'POINT({longitude:.6f} {latitude:.6f})'
            ) p
            JOIN component c ON c.mukey = p.mukey
            WHERE c.comppct_r > 0
                AND EXISTS (
                    SELECT 1 FROM chorizon s WHERE s.cokey = c.cokey
                    AND s.hzdept_r <= 5 AND s.hzdepb_r > 5
                )
            ORDER BY c.comppct_r DESC, p.mukey, c.cokey
        )
        SELECT r.mukey, r.cokey, r.slope_r, r.drainagecl, {horizons}
        FROM representative r JOIN chorizon h ON h.cokey = r.cokey
        WHERE h.hzdept_r < 100 AND h.hzdepb_r > 0
        ORDER BY h.hzdept_r, h.hzdepb_r, h.chkey
    """


def _number(value, maximum: float) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not isfinite(number) or not 0 <= number <= maximum:
        raise ValueError("SSURGO numeric attribute out of bounds")
    return number


def _mean_attribute(horizons: list[tuple], column: int, top: float, bottom: float) -> float | None:
    # Overlapping transitional horizons cover each interval only once.
    edges = sorted(
        {top, bottom, *(edge for row in horizons for edge in row[:2] if top < edge < bottom)}
    )
    covered = weighted = 0.0
    for start, end in zip(edges[:-1], edges[1:], strict=True):
        values = [
            row[column]
            for row in horizons
            if row[0] <= start and end <= row[1] and row[column] is not None
        ]
        if values:
            covered += end - start
            weighted += (end - start) * sum(values) / len(values)
    return weighted / covered if covered >= 0.7 * (bottom - top) else None


def parse_root_profile(document: dict) -> dict[str, float | None] | None:
    if not isinstance(document, dict):
        raise ValueError("Unexpected SSURGO document")
    table = document.get("Table")
    if not document or table in ([], [list(COLUMNS)]):
        return None
    if not isinstance(table, list) or table[0] != list(COLUMNS):
        raise ValueError("Unexpected SSURGO tabular response")
    first = table[1][:4]
    horizons = []
    for row in table[1:]:
        if len(row) != len(COLUMNS) or row[:4] != first:
            raise ValueError("SSURGO returned inconsistent map-unit components")
        upper, lower, *attributes = row[4:]
        top, bottom = float(upper), float(lower)
        if not isfinite(top) or not isfinite(bottom) or not 0 <= top < bottom:
            raise ValueError("SSURGO returned an invalid horizon")
        properties = [
            _number(value, limit)
            for value, limit in zip(attributes, (100, 100, 100, 3, 1, 25000), strict=True)
        ]
        if properties[-1] is not None:
            properties[-1] = log1p(properties[-1])
        horizons.append((top, bottom, *properties))

    sand, clay, organic, density, awc, conductivity = (
        _mean_attribute(horizons, column, 0, 100) for column in range(2, 8)
    )
    shallow_clay = _mean_attribute(horizons, 3, 0, 30)
    deep_clay = _mean_attribute(horizons, 3, 30, 100)
    edges = sorted({0, 100, *(edge for row in horizons for edge in row[:2] if 0 < edge < 100)})
    coverage = (
        sum(
            end - start
            for start, end in zip(edges[:-1], edges[1:], strict=True)
            if any(row[0] <= start and end <= row[1] for row in horizons)
        )
        / 100
    )
    drainage = first[3]
    values = (
        sand,
        clay,
        organic,
        density,
        None if awc is None else 1000 * awc,
        conductivity,
        None if shallow_clay is None or deep_clay is None else deep_clay - shallow_clay,
        coverage,
        _number(first[2], 100),
        DRAINAGE_CLASSES.index(drainage) if drainage in DRAINAGE_CLASSES else None,
    )
    return dict(zip(SOIL_FEATURES, values, strict=True))


def download_profile(site: RootZoneSite, cache: Path) -> tuple[dict | None, str]:
    query = soil_query(site.latitude, site.longitude)
    path = cache / (sha256(query.encode()).hexdigest() + ".json")
    if path.is_file():
        raw = path.read_bytes()
    else:
        request = Request(
            SDA_ENDPOINT,
            data=json.dumps({"query": query, "format": "JSON+COLUMNNAME"}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "FieldShift-Research/1.0"},
        )
        with urlopen(request, timeout=20) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("SSURGO horizon response exceeds the research size bound")
    document = json.loads(raw)
    profile = parse_root_profile(document)
    if not path.exists():
        write_atomic(path, raw)
    return profile, sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, _ = load_targets(args.observations)
    sites = [
        RootZoneSite(**{key: value for key, value in item.items() if key != "days"})
        for item in manifest["sites"]
        if item["days"] and item["role"] != "test"
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        profiles = dict(
            zip(
                (site.station_id for site in sites),
                pool.map(lambda site: download_profile(site, args.cache), sites),
                strict=True,
            )
        )
    snapshot = {
        "source": SDA_ENDPOINT,
        "sites": {
            station: {"features": profile, "source_sha256": digest}
            for station, (profile, digest) in profiles.items()
        },
    }
    write_atomically(args.output, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    available = sum(profile is not None for profile, _ in profiles.values())
    print(f"Stored {available}/{len(sites)} profiles")


if __name__ == "__main__":
    main()
