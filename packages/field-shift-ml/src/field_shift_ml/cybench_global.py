"""Bounded, retrospective long-lead CY-Bench evaluation; never a serving model.

Run with ``python -m field_shift_ml.cybench_global`` from an installed package.
The protocol is frozen before preparation/evaluation. Raw data and predictions
belong in an explicit external cache, not the installed distribution.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import math
import platform
import resource
import struct
import sys
import time
import urllib.request
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

PANELS = (
    ("maize", "BR"),
    ("maize", "CN"),
    ("maize", "FR"),
    ("maize", "US"),
    ("wheat", "CN"),
    ("wheat", "FR"),
)
TRAINING_PANELS = (("maize", "FR"), ("maize", "US"), ("wheat", "FR"))
SOURCE_RIGHTS = {
    "FR": {
        "fit_eligible": True,
        "terms": "CC-BY-4.0; attribution required",
        "source": "https://data.jrc.ec.europa.eu/dataset/685949ff-56de-4646-a8df-844b5bb5f835",
    },
    "US": {
        "fit_eligible": True,
        "terms": "USDA NASS public data; upstream card declares CC0",
        "source": "https://quickstats.nass.usda.gov/",
    },
    "CN": {
        "fit_eligible": False,
        "reason": "restricted_terms",
        "terms": "upstream source card permits noncommercial use; excluded from all fitting",
        "source": "https://data.stats.gov.cn/english/staticreq.htm?m=aboutctryinfo",
    },
    "BR": {
        "fit_eligible": False,
        "reason": "unverified_terms",
        "terms": "no sufficiently clear IBGE dataset reuse grant verified; inventory only",
        "source": "https://www.ibge.gov.br/acesso-informacao/acoes-e-programas/termos-de-uso.html",
    },
}
WEATHER = ("tmin", "tmax", "tavg", "prec", "rad", "et0", "vpd")
SUM_VARIABLES = {"prec", "rad", "et0"}
STATIC = ("latitude", "longitude", "awc", "bulk_density", "drainage_class")
FEATURES = (
    "harvest_year",
    *STATIC,
    *(f"recent_90d_{x}" for x in WEATHER),
    *(f"climatology_annual_{x}" for x in WEATHER),
)
MODELS = ("country_mean", "country_trend", "ridge", "hist_gradient_boosting")
ARCHIVE_URL = "https://zenodo.org/api/records/17279151/files/cybench-data.zip/content"
ARCHIVE_SIZE = 6_228_749_411
ARCHIVE_DECLARED_MD5 = "8ec510982f396469eca25a4f0fa8632e"
PROTOCOL = {
    "id": "cybench-global-long-lead-v2",
    "supersedes_protocol_sha256": (
        "80cafa05d20a9a118e95a037052230d8c2786ad9b3b3b2c1d6cbc0addfb3b90a"
    ),
    "revision_reason": "source-rights filter before any fitting or error inspection",
    "training_panels": [list(p) for p in TRAINING_PANELS],
    "source_rights": SOURCE_RIGHTS,
    "record": "https://zenodo.org/records/17279151",
    "dataset_version": "1.10",
    "panels": [list(p) for p in PANELS],
    "test_harvest_years": [2018, 2019, 2020],
    "training_first_harvest_year": 2008,
    "issue_date": "December 31 of harvest_year minus 2",
    "label_availability_assumption": "December 31 of label harvest_year plus 1",
    "label_availability_rule": "availability <= issue_date",
    "weather_lag_days": 14,
    "recent_weather_days": 90,
    "climatology": "five complete calendar years ending harvest_year minus 3",
    "weather_aggregation": {
        "recent": "sum prec/rad/et0; mean tmin/tmax/tavg/vpd",
        "climatology": "mean annual sum prec/rad/et0; day-weighted mean others",
    },
    "features": list(FEATURES),
    "excluded_features": [
        "target-year production",
        "target-year harvested area",
        "crop mask",
        "calendar SOS",
        "in-season observations",
    ],
    "tracks": ["chronological", "spatial_chronological"],
    "spatial_groups": {"US": "state", "BR": "state", "FR": "NUTS2", "CN": "province"},
    "spatial_holdout": "int(SHA256('cybench-global-v1:'+country+':'+group),16) % 5 == 0",
    "labels": "finite yield >= 0 retained; negative/nonfinite/missing excluded and counted",
    "missing_features": "exclude and count; no imputation",
    "weather_quality": "unique ordered dates per region; complete windows; finite values; "
    "prec/et0/rad/vpd >= 0; tmin <= tmax",
    "models": {
        "country_mean": "mean of eligible training region-year yields",
        "country_trend": "OLS of unweighted annual training-country mean yield on year",
        "ridge": {"alpha": 10.0, "standardization": "training only"},
        "hist_gradient_boosting": {
            "max_iter": 100,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 20,
            "l2_regularization": 1.0,
            "early_stopping": False,
            "random_state": 0,
        },
    },
    "prediction_constraint": "clip every model prediction to >= 0 before scoring",
    "tuning": "none",
    "score_units": "t/ha; NRMSE percent of the test mean; no MAPE because zeros are valid",
    "minimum_rows": {"train": 20, "test": 1},
    "threads": 2,
    "status": "retrospective diagnostic; not observed pre-sowing or prospective validation",
    "limitations": [
        "static source maps were reconstructed after the historical issue dates",
        "actual historical publication timestamps are not supplied",
        "administrative yield is not field yield",
        "three common test years do not establish global or extreme-year skill",
        "published CY-Bench baselines have different versions/cutoffs/splits",
    ],
}


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def freeze_protocol(path: Path) -> str:
    frozen = {"protocol": PROTOCOL, "sha256": canonical_hash(PROTOCOL)}
    if path.exists() and json.loads(path.read_text()) != frozen:
        raise ValueError("refusing to replace a different frozen protocol")
    write_json(path, frozen)
    return frozen["sha256"]


def check_protocol(path: Path) -> str:
    frozen = json.loads(path.read_text())
    expected = canonical_hash(PROTOCOL)
    if frozen != {"protocol": PROTOCOL, "sha256": expected}:
        raise ValueError("protocol differs from the frozen implementation")
    return expected


def issue_date(harvest_year: int) -> date:
    return date(harvest_year - 2, 12, 31)


def label_available(label_year: int, target_year: int) -> bool:
    return date(label_year + 1, 12, 31) <= issue_date(target_year)


def weather_window(harvest_year: int) -> tuple[date, date]:
    end = issue_date(harvest_year) - timedelta(days=PROTOCOL["weather_lag_days"])
    return end - timedelta(days=PROTOCOL["recent_weather_days"] - 1), end


def region_group(country: str, adm: str) -> str:
    if country == "US" and len(adm.split("-")) == 3:
        return "-".join(adm.split("-")[:2])
    if country == "BR" and adm.startswith("BR") and adm[2:].isdigit():
        return adm[:4]
    if country == "FR" and adm.startswith("FR") and len(adm) == 5:
        return adm[:4]
    if country == "CN" and adm.startswith("CN"):
        return adm
    raise ValueError(f"unsupported administrative identifier: {country}/{adm}")


def is_spatial_holdout(country: str, group: str) -> bool:
    key = f"cybench-global-v1:{country}:{group}"
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 5 == 0


def file_digest(path: Path) -> tuple[str, str, int]:
    sha = hashlib.sha256()
    crc = 0
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            sha.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
    return sha.hexdigest(), f"{crc:08x}", size


def open_range(start: int, end: int):
    request = urllib.request.Request(
        ARCHIVE_URL,
        headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
    )
    response = urllib.request.urlopen(request, timeout=60)
    if (
        response.status != 206
        or response.headers.get("Content-Range") != f"bytes {start}-{end}/{ARCHIVE_SIZE}"
    ):
        response.close()
        raise ValueError("server did not honor the bounded archive range")
    return response


class ArchiveTail(io.RawIOBase):
    """Seekable ZIP64 central directory without retrieving the entire archive."""

    def __init__(self, tail: bytes):
        self.tail = tail
        self.start = ARCHIVE_SIZE - len(tail)
        self.position = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        self.position = offset + (
            0 if whence == 0 else self.position if whence == 1 else ARCHIVE_SIZE
        )
        return self.position

    def tell(self) -> int:
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self.position < self.start:
            raise ValueError("ZIP central directory exceeds the bounded tail")
        index = self.position - self.start
        size = ARCHIVE_SIZE - self.position if size < 0 else size
        block = self.tail[index : index + size]
        self.position += len(block)
        return block

    def seekable(self) -> bool:
        return True


def fetch_member(info: zipfile.ZipInfo, data_root: Path) -> dict:
    # Only acquire()'s exact allowlisted names reach this function.
    relative = Path(info.filename)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "cybench-data":
        raise ValueError("unsafe ZIP member path")
    path = data_root.joinpath(*relative.parts[1:])
    manifest_path = path.with_suffix(".manifest.json")
    if path.exists() and manifest_path.exists():
        verify_member(path)
        return json.loads(manifest_path.read_text())
    if info.compress_type != zipfile.ZIP_DEFLATED or info.file_size > 4 * 1024**3:
        raise ValueError("unsupported or oversized ZIP member")
    start, end = info.header_offset, info.header_offset + info.compress_size + 1023
    if end >= ARCHIVE_SIZE:
        raise ValueError("member range exceeds archive")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    range_sha, compressed_sha, sha = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    crc = downloaded = written = 0
    decoder = zlib.decompressobj(-15)
    with open_range(start, end) as response, partial.open("wb") as output:
        prefix = response.read(1024)
        downloaded += len(prefix)
        range_sha.update(prefix)
        if len(prefix) != 1024 or prefix[:4] != b"PK\x03\x04":
            raise ValueError("invalid ZIP local header")
        name_size, extra_size = struct.unpack_from("<HH", prefix, 26)
        offset = 30 + name_size + extra_size
        if offset > 1024 or prefix[30 : 30 + name_size].decode() != info.filename:
            raise ValueError("ZIP local/central header disagreement")
        remaining, block = info.compress_size, prefix[offset:]
        while remaining:
            compressed = block[:remaining]
            remaining -= len(compressed)
            compressed_sha.update(compressed)
            while compressed:
                uncompressed = decoder.decompress(compressed, 16 * 1024**2)
                compressed = decoder.unconsumed_tail
                written += len(uncompressed)
                if written > info.file_size:
                    raise ValueError("ZIP expansion exceeds declared size")
                crc = zlib.crc32(uncompressed, crc)
                sha.update(uncompressed)
                output.write(uncompressed)
            if remaining:
                block = response.read(min(2 * 1024**2, remaining))
                if not block:
                    raise ValueError("truncated archive range")
                downloaded += len(block)
                range_sha.update(block)
        suffix = response.read(1024)
        downloaded += len(suffix)
        range_sha.update(suffix)
        if response.read(1) or downloaded != end - start + 1:
            raise ValueError("archive range length mismatch")
    if not decoder.eof or written != info.file_size or crc != info.CRC:
        raise ValueError("ZIP member CRC/size mismatch")
    partial.replace(path)
    manifest = {
        "name": info.filename,
        "size": written,
        "compressed": info.compress_size,
        "offset": info.header_offset,
        "method": info.compress_type,
        "crc32": f"{crc:08x}",
        "url": ARCHIVE_URL,
        "range": [start, end],
        "downloaded_bytes": downloaded,
        "range_sha256": range_sha.hexdigest(),
        "compressed_sha256": compressed_sha.hexdigest(),
        "sha256": sha.hexdigest(),
        "crc_verified": True,
        "version": "1.10",
        "record": PROTOCOL["record"],
        "archive_md5_verified": False,
    }
    write_json(manifest_path, manifest)
    return manifest


def acquire(data_root: Path, panels: list[tuple[str, str]]) -> dict:
    tail_size = 131072
    with open_range(ARCHIVE_SIZE - tail_size, ARCHIVE_SIZE - 1) as response:
        tail = response.read(tail_size + 1)
    if len(tail) != tail_size:
        raise ValueError("archive tail length mismatch")
    with zipfile.ZipFile(ArchiveTail(tail)) as archive:
        names = [
            f"cybench-data/{crop}/{country}/{kind}_{crop}_{country}.csv"
            for crop, country in panels
            for kind in ("yield", "soil", "location", "meteo")
        ]
        members = [archive.getinfo(name) for name in names]
    requested_bytes = tail_size + sum(info.compress_size + 1024 for info in members)
    if requested_bytes > 3 * 1024**3 or sum(info.file_size for info in members) > 8 * 1024**3:
        raise ValueError("selected archive members exceed the acquisition budget")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        manifests = list(pool.map(lambda info: fetch_member(info, data_root), members))
    result = {
        "archive_url": ARCHIVE_URL,
        "archive_size": ARCHIVE_SIZE,
        "archive_declared_md5": ARCHIVE_DECLARED_MD5,
        "archive_md5_verified": False,
        "tail_sha256": hashlib.sha256(tail).hexdigest(),
        "requested_bytes": requested_bytes,
        "license_record": "EUPL-1.2; contributing sources have their own terms",
        "members": manifests,
    }
    write_json(data_root / "acquisition.json", result)
    return result


def verify_member(path: Path) -> dict:
    """Verify extracted bytes against the recorded HTTP Range/ZIP member identity."""
    manifest = json.loads(path.with_suffix(".manifest.json").read_text())
    sha, crc, size = file_digest(path)
    if (sha, crc, size) != (manifest["sha256"], manifest["crc32"], manifest["size"]):
        raise ValueError(f"source member checksum mismatch: {path}")
    if (
        not manifest["crc_verified"]
        or manifest["version"] != PROTOCOL["dataset_version"]
        or manifest["record"] != PROTOCOL["record"]
        or Path(manifest["name"]).name != path.name
    ):
        raise ValueError("unverified or wrong-version source member")
    return {
        k: manifest[k]
        for k in (
            "name",
            "size",
            "compressed",
            "offset",
            "method",
            "crc32",
            "url",
            "range",
            "range_sha256",
            "compressed_sha256",
            "sha256",
            "record",
            "version",
            "archive_md5_verified",
        )
    }


def read_unique_rows(path: Path) -> dict[str, dict]:
    result = {}
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            adm = row["adm_id"]
            if adm in result:
                raise ValueError(f"duplicate static region {adm} in {path}")
            result[adm] = row
    return result


def read_labels(path: Path) -> tuple[list[dict], dict]:
    counts: Counter = Counter()
    labels = []
    seen = set()
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            counts["source_rows"] += 1
            try:
                year = int(row["harvest_year"])
                value = float(row["yield"])
            except ValueError:
                counts["missing_or_invalid_label"] += 1
                continue
            if not math.isfinite(value) or value < 0:
                counts["negative_or_nonfinite_label"] += 1
                continue
            if (row["adm_id"], year) in seen:
                raise ValueError("duplicate region-year yield; no implicit averaging")
            seen.add((row["adm_id"], year))
            counts["zero_yield" if value == 0 else "positive_yield"] += 1
            if not 2008 <= year <= 2020:
                counts["outside_protocol_years"] += 1
                continue
            labels.append({"adm_id": row["adm_id"], "year": year, "yield": value})
    counts["protocol_label_rows"] = len(labels)
    return labels, dict(counts)


def _add(totals: dict, key: tuple, values: tuple[float, ...]) -> None:
    if key not in totals:
        totals[key] = [0.0] * len(values) + [0]
    acc = totals[key]
    for index, value in enumerate(values):
        acc[index] += value
    acc[-1] += 1


def aggregate_weather(path: Path, eligible_regions: set[str]) -> tuple[dict, dict, dict]:
    """Stream ordered daily CSV, retaining only small region/year aggregates.

    Dates after the latest declared cutoff are not read into feature statistics.
    Complete-file SHA/CRC verification is separate and includes those bytes.
    """
    annual: dict = {}
    recent: dict = {}
    counts: Counter = Counter()
    windows = {year: weather_window(year) for year in range(2008, 2021)}
    last_date = None
    current_ids: set[str] = set()
    with path.open(newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        indexes = [header.index(name) for name in WEATHER]
        date_index, adm_index = header.index("date"), header.index("adm_id")
        for row in reader:
            text_date = row[date_index]
            if last_date is not None and text_date < last_date:
                raise ValueError("weather must be globally ordered by date")
            if text_date != last_date:
                current_ids.clear()
                day = date(int(text_date[:4]), int(text_date[4:6]), int(text_date[6:8]))
                if day > windows[2020][1]:
                    break
                last_date = text_date
            adm = row[adm_index]
            if adm in current_ids:
                raise ValueError(f"duplicate weather observation {adm}/{text_date}")
            current_ids.add(adm)
            counts["rows_before_latest_cutoff"] += 1
            if adm not in eligible_regions:
                counts["rows_without_eligible_label_region"] += 1
                continue
            try:
                values = tuple(float(row[index]) for index in indexes)
            except ValueError:
                counts["invalid_weather_rows"] += 1
                continue
            if (
                not all(math.isfinite(v) for v in values)
                or any(values[i] < 0 for i in (3, 4, 5, 6))
                or values[0] > values[1]
            ):
                counts["invalid_weather_rows"] += 1
                continue
            if 2001 <= day.year <= 2017:
                _add(annual, (adm, day.year), values)
            target_year = day.year + 2
            if target_year in windows and windows[target_year][0] <= day <= windows[target_year][1]:
                _add(recent, (adm, target_year), values)
    counts["annual_region_years"] = len(annual)
    counts["recent_region_years"] = len(recent)
    return annual, recent, dict(counts)


def make_weather_features(adm: str, year: int, annual: dict, recent: dict) -> list | None:
    latest = recent.get((adm, year))
    if latest is None or latest[-1] != 90:
        return None
    history = []
    for old_year in range(year - 7, year - 2):
        values = annual.get((adm, old_year))
        days = (date(old_year + 1, 1, 1) - date(old_year, 1, 1)).days
        if values is None or values[-1] != days:
            return None
        history.append(values)
    result = [latest[i] / (1 if name in SUM_VARIABLES else 90) for i, name in enumerate(WEATHER)]
    count = sum(row[-1] for row in history)
    result.extend(
        sum(row[i] for row in history) / (5 if name in SUM_VARIABLES else count)
        for i, name in enumerate(WEATHER)
    )
    return result


def prepare_panel(
    data_root: Path, output: Path, crop: str, country: str, protocol_hash: str
) -> dict:
    folder = data_root / crop / country
    paths = {
        kind: folder / f"{kind}_{crop}_{country}.csv"
        for kind in ("yield", "soil", "location", "meteo")
    }
    identities = {kind: verify_member(path) for kind, path in paths.items()}
    labels, counts = read_labels(paths["yield"])
    soils, locations = read_unique_rows(paths["soil"]), read_unique_rows(paths["location"])
    annual, recent, weather_counts = aggregate_weather(
        paths["meteo"], {row["adm_id"] for row in labels}
    )
    exclusions: Counter = Counter()
    year_counts: Counter = Counter()
    zero_counts: Counter = Counter()
    groups: Counter = Counter()
    feature_path = output / f"{crop}_{country}.csv"
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    with feature_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "year", "group", "yield", *FEATURES])
        for label in labels:
            adm, year = label["adm_id"], label["year"]
            if adm not in soils or adm not in locations:
                exclusions["missing_static_region"] += 1
                continue
            try:
                static = [float((locations[adm] | soils[adm])[key]) for key in STATIC]
            except (ValueError, KeyError):
                exclusions["missing_static_value"] += 1
                continue
            if not all(math.isfinite(v) for v in static):
                exclusions["nonfinite_static_value"] += 1
                continue
            weather = make_weather_features(adm, year, annual, recent)
            if weather is None:
                exclusions["incomplete_weather_window"] += 1
                continue
            group = region_group(country, adm)
            writer.writerow([adm, year, group, label["yield"], year, *static, *weather])
            year_counts[year] += 1
            zero_counts[year] += label["yield"] == 0
            groups[group] += 1
    metadata = {
        "implementation_sha256": file_digest(Path(__file__))[0],
        "protocol_sha256": protocol_hash,
        "crop": crop,
        "country": country,
        "source_members": identities,
        "label_counts": counts,
        "weather_counts": weather_counts,
        "excluded": dict(exclusions),
        "eligible_by_year": dict(year_counts),
        "zero_yields_by_year": dict(zero_counts),
        "groups": dict(groups),
        "holdout_groups": sorted(g for g in groups if is_spatial_holdout(country, g)),
        "features_sha256": file_digest(feature_path)[0],
    }
    write_json(feature_path.with_suffix(".json"), metadata)
    return metadata


def scores(actual: np.ndarray, predicted: np.ndarray) -> dict:
    error = predicted - actual
    mse = float(np.mean(error**2))
    mean = float(np.mean(actual))
    total = float(np.sum((actual - mean) ** 2))
    return {
        "n": len(actual),
        "mae_t_ha": float(np.mean(np.abs(error))),
        "rmse_t_ha": math.sqrt(mse),
        "bias_t_ha": float(np.mean(error)),
        "mean_yield_t_ha": mean,
        "nrmse_percent": 100 * math.sqrt(mse) / mean if mean > 0 else None,
        "r2": 1 - float(np.sum(error**2)) / total if np.ptp(actual) != 0 else None,
    }


def fit_predict(
    model: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    years: np.ndarray,
    x_test: np.ndarray,
    target_year: int,
) -> np.ndarray:
    if model == "country_mean":
        raw = np.full(len(x_test), np.mean(y_train))
    elif model == "country_trend":
        unique = np.unique(years)
        means = np.array([np.mean(y_train[years == year]) for year in unique])
        slope, intercept = np.polyfit(unique - unique[0], means, 1)
        raw = np.full(len(x_test), slope * (target_year - unique[0]) + intercept)
    elif model == "ridge":
        fitted = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
        raw = fitted.fit(x_train, y_train).predict(x_test)
    elif model == "hist_gradient_boosting":
        fitted = HistGradientBoostingRegressor(**PROTOCOL["models"][model])
        raw = fitted.fit(x_train, y_train).predict(x_test)
    else:
        raise ValueError(f"unknown model {model}")
    if not np.isfinite(raw).all():
        raise ValueError("model produced nonfinite predictions")
    return np.maximum(raw, 0)


def evaluate_panel(
    prepared: Path, predictions_dir: Path, crop: str, country: str, protocol_hash: str
) -> dict:
    if (crop, country) not in TRAINING_PANELS:
        raise ValueError(f"panel excluded from fitting by source-rights protocol: {crop}/{country}")
    path = prepared / f"{crop}_{country}.csv"
    metadata = json.loads(path.with_suffix(".json").read_text())
    if (
        metadata["protocol_sha256"] != protocol_hash
        or metadata["features_sha256"] != file_digest(path)[0]
    ):
        raise ValueError("prepared features/protocol checksum mismatch")
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    years = np.array([int(row["year"]) for row in rows])
    y = np.array([float(row["yield"]) for row in rows])
    x = np.array([[float(row[name]) for name in FEATURES] for row in rows])
    held_out = np.array([is_spatial_holdout(country, row["group"]) for row in rows])
    predictions_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = predictions_dir / f"{crop}_{country}.csv"
    results = []
    aggregate: dict = defaultdict(lambda: [[], []])
    with prediction_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "track",
                "model",
                "adm_id",
                "group",
                "harvest_year",
                "issue_date",
                "actual_yield_t_ha",
                "predicted_yield_t_ha",
            ]
        )
        for track in PROTOCOL["tracks"]:
            for year in PROTOCOL["test_harvest_years"]:
                train = np.array([label_available(int(old), year) for old in years])
                test = years == year
                if track == "spatial_chronological":
                    train &= ~held_out
                    test &= held_out
                fold = {
                    "track": track,
                    "harvest_year": year,
                    "issue_date": issue_date(year).isoformat(),
                    "train_rows": int(train.sum()),
                    "test_rows": int(test.sum()),
                    "train_groups": sorted({rows[i]["group"] for i in np.flatnonzero(train)}),
                    "test_groups": sorted({rows[i]["group"] for i in np.flatnonzero(test)}),
                }
                if (
                    train.sum() < PROTOCOL["minimum_rows"]["train"]
                    or test.sum() < PROTOCOL["minimum_rows"]["test"]
                ):
                    results.append(fold | {"status": "insufficient_rows"})
                    continue
                fold["train_target_years"] = sorted(int(v) for v in np.unique(years[train]))
                for model in MODELS:
                    started = time.monotonic()
                    predicted = fit_predict(model, x[train], y[train], years[train], x[test], year)
                    results.append(
                        fold
                        | {
                            "model": model,
                            "status": "evaluated",
                            "scores": scores(y[test], predicted),
                            "fit_predict_seconds": time.monotonic() - started,
                        }
                    )
                    aggregate[(track, model)][0].extend(y[test].tolist())
                    aggregate[(track, model)][1].extend(predicted.tolist())
                    for index, value in zip(np.flatnonzero(test), predicted, strict=True):
                        row = rows[index]
                        writer.writerow(
                            [
                                track,
                                model,
                                row["adm_id"],
                                row["group"],
                                year,
                                issue_date(year).isoformat(),
                                y[index],
                                value,
                            ]
                        )
    return {
        "crop": crop,
        "country": country,
        "coverage": metadata,
        "folds": results,
        "pooled_within_panel": [
            {"track": track, "model": model, "scores": scores(np.array(a), np.array(p))}
            for (track, model), (a, p) in aggregate.items()
        ],
        "predictions_sha256": file_digest(prediction_path)[0],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "acquire", "prepare", "evaluate"))
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--panel", choices=[f"{c}_{n}" for c, n in PANELS], action="append")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        print(freeze_protocol(args.protocol))
        return 0
    protocol_hash = check_protocol(args.protocol)
    panels = [tuple(p.split("_")) for p in args.panel] if args.panel else list(TRAINING_PANELS)
    if args.command in {"prepare", "evaluate"} and any(p not in TRAINING_PANELS for p in panels):
        parser.error("panel excluded from preparation/fitting by source-rights protocol")
    if args.command == "acquire":
        if args.data_root is None:
            parser.error("--data-root is required")
        print(json.dumps({"requested_bytes": acquire(args.data_root, panels)["requested_bytes"]}))
        return 0
    if args.prepared is None:
        parser.error("--prepared is required")
    if args.command == "prepare":
        if args.data_root is None:
            parser.error("--data-root is required")
        for crop, country in panels:
            result = prepare_panel(args.data_root, args.prepared, crop, country, protocol_hash)
            print(
                json.dumps(
                    {"crop": crop, "country": country, "eligible": result["eligible_by_year"]}
                ),
                flush=True,
            )
        return 0
    if args.output is None or args.predictions is None:
        parser.error("--output and --predictions are required")
    started = time.monotonic()
    report = {
        "implementation_sha256": file_digest(Path(__file__))[0],
        "protocol": PROTOCOL,
        "protocol_sha256": protocol_hash,
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "sklearn_version": sklearn.__version__,
        "panel_inventory": [
            {"crop": crop, "country": country, **SOURCE_RIGHTS[country]} for crop, country in PANELS
        ],
        "panels": [],
    }
    with threadpool_limits(limits=PROTOCOL["threads"]):
        for crop, country in panels:
            report["panels"].append(
                evaluate_panel(args.prepared, args.predictions, crop, country, protocol_hash)
            )
            print(f"evaluated {crop}/{country}", flush=True)
    report["elapsed_seconds"] = time.monotonic() - started
    report["peak_rss_kib_linux"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
