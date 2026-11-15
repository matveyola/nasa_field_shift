"""Predeclared US maize parameter search with separately frozen outer evaluation.

This offline diagnostic preserves cybench_global and uses no serving estimator.
Explicit external inputs/outputs and an installed package are required.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import resource
import signal
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import (
    ExtraTreesRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from field_shift_ml import cybench_global as base


def candidates() -> list[dict]:
    result = [
        {"id": "country_mean", "family": "country_mean", "parameters": {}},
        {"id": "country_trend", "family": "country_trend", "parameters": {}},
    ]
    for alpha in (0.1, 1.0, 10.0, 100.0):
        result.append(
            {"id": f"ridge_a{alpha:g}", "family": "ridge", "parameters": {"alpha": alpha}}
        )
    for leaves, rate, penalty in product((7, 15), (0.05, 0.1), (1.0, 10.0)):
        result.append(
            {
                "id": f"hgb_l{leaves}_r{rate:g}_p{penalty:g}",
                "family": "hist_gradient_boosting",
                "parameters": {
                    "max_iter": 100,
                    "max_leaf_nodes": leaves,
                    "learning_rate": rate,
                    "l2_regularization": penalty,
                    "min_samples_leaf": 20,
                    "early_stopping": False,
                    "random_state": 0,
                },
            }
        )
    for family in ("random_forest", "extra_trees"):
        for leaf, fraction in product((5, 20), (0.7, 1.0)):
            result.append(
                {
                    "id": f"{family}_l{leaf}_f{fraction:g}",
                    "family": family,
                    "parameters": {
                        "n_estimators": 200,
                        "max_depth": 16,
                        "min_samples_leaf": leaf,
                        "max_features": fraction,
                        "random_state": 0,
                        "n_jobs": 2,
                        "bootstrap": family == "random_forest",
                    },
                }
            )
    return result


CANDIDATES = candidates()
FAMILIES = tuple(dict.fromkeys(c["family"] for c in CANDIDATES))
PROTOCOL = {
    "id": "cybench-us-maize-search-v1",
    "dataset": "https://zenodo.org/records/17279151",
    "dataset_version": "1.10",
    "panel": ["maize", "US"],
    "rights": base.SOURCE_RIGHTS["US"],
    "preserved_baseline_protocol_sha256": base.canonical_hash(base.PROTOCOL),
    "features": list(base.FEATURES),
    "feature_policy": "unchanged baseline v2: 90d ending issue minus14d; five calendar years "
    "ending Y-3; finite complete static/weather, no imputation; retain zero yields",
    "issue_date": "December31 of harvest year minus2; not observed sowing",
    "assumed_label_availability": "December31 of label harvest year plus1 <= issue date",
    "first_training_year": 2008,
    "inner_validation_years": [2014, 2015, 2016, 2017],
    "outer_test_years": [2021, 2022, 2023],
    "opened_test_years_excluded_from_selection": [2018, 2019, 2020],
    "inner_training_rule": "2008 through validation year minus3 inclusive",
    "outer_training_rule": "2008 through test year minus3 inclusive",
    "tracks": ["chronological", "spatial_chronological"],
    "state_holdout": base.PROTOCOL["spatial_holdout"],
    "spatial_development": (
        "exclude held-out states from all inner train/validation and outer train"
    ),
    "spatial_outer_test": "only held-out states in the three outer years",
    "candidates": CANDIDATES,
    "selection_score": "unweighted mean of four inner-year RMSE values in t/ha",
    "tie_break": "first candidate in declared order on exactly equal mean RMSE",
    "outer_outputs": "inner-selected best per family and both baselines; overall selection "
    "fixed before all outer fits/errors; no post-test promotion",
    "reuse_selection": "same four inner folds per track for all outer years; compute once",
    "prediction_constraint": "all candidate predictions clipped >=0",
    "minimum_rows": {"train": 20, "validation_or_test": 1},
    "bounds": {
        "inner_fits": 176,
        "outer_fits": 36,
        "threads": 2,
        "processes": 1,
        "address_space_bytes": 2 * 1024**3,
        "wall_seconds": 1200,
    },
    "failure_policy": "abort entire search on any candidate failure/nonfinite output or bound; "
    "never select among an incomplete candidate set",
    "holdout_status": "public raw labels and eligibility counts inventoried; outer errors "
    "not inspected before selection; not externally sealed",
    "limitations": [
        "retrospective static maps and reanalysis; actual historical publication times unknown",
        "administrative maize yield in one country, not farm yield or global accuracy",
        "three outer years and one state partition; no calibrated prediction intervals",
        "published CY-Bench mid-season LOYO tables are a different target and protocol",
    ],
}


def identity() -> dict:
    return {
        "search_module_sha256": base.file_digest(Path(__file__))[0],
        "baseline_module_sha256": base.file_digest(Path(base.__file__))[0],
    }


def write_once(path: Path, value: dict) -> None:
    """Do not replace a previous protocol/selection, including on rerun failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f"refusing to replace frozen file: {path}")
        return
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def freeze(path: Path) -> str:
    digest = base.canonical_hash(PROTOCOL)
    write_once(path, {"protocol": PROTOCOL, "sha256": digest})
    return digest


def check_protocol(path: Path) -> str:
    digest = base.canonical_hash(PROTOCOL)
    if json.loads(path.read_text()) != {"protocol": PROTOCOL, "sha256": digest}:
        raise ValueError("protocol differs from frozen search")
    return digest


def labels(path: Path) -> tuple[list[dict], dict]:
    result, seen, counts = [], set(), Counter()
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            counts["source_rows"] += 1
            try:
                year, value = int(row["harvest_year"]), float(row["yield"])
            except ValueError:
                counts["missing_or_invalid_label"] += 1
                continue
            if not math.isfinite(value) or value < 0:
                counts["negative_or_nonfinite_label"] += 1
                continue
            key = row["adm_id"], year
            if key in seen:
                raise ValueError("duplicate region-year yield")
            seen.add(key)
            counts["zero_yield" if value == 0 else "positive_yield"] += 1
            if not 2008 <= year <= 2023:
                counts["outside_protocol_years"] += 1
                continue
            result.append({"adm_id": key[0], "year": year, "yield": value})
    return result, dict(counts)


def weather_counts(path: Path, regions: set[str]) -> tuple[dict, dict, dict]:
    """Stream complete historical windows for 2008–2023; no learned preprocessing."""
    annual, recent, counts = {}, {}, Counter()
    windows = {y: base.weather_window(y) for y in range(2008, 2024)}
    last, seen = None, set()
    with path.open(newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        indexes = [header.index(k) for k in base.WEATHER]
        ai, di = header.index("adm_id"), header.index("date")
        for row in reader:
            text_date = row[di]
            if len(text_date) != 8 or not text_date.isdigit():
                raise ValueError("weather date must be YYYYMMDD")
            if last is not None and text_date < last:
                raise ValueError("weather dates must be ordered")
            if text_date != last:
                seen.clear()
                day = date(int(text_date[:4]), int(text_date[4:6]), int(text_date[6:]))
                if day > windows[2023][1]:
                    break
                last = text_date
            adm = row[ai]
            if adm in seen:
                raise ValueError("duplicate daily weather")
            seen.add(adm)
            counts["rows_before_latest_cutoff"] += 1
            if adm not in regions:
                counts["rows_without_eligible_label_region"] += 1
                continue
            try:
                values = tuple(float(row[i]) for i in indexes)
            except ValueError:
                counts["invalid_weather_rows"] += 1
                continue
            if (
                not all(math.isfinite(v) for v in values)
                or values[0] > values[1]
                or any(values[i] < 0 for i in (3, 4, 5, 6))
            ):
                counts["invalid_weather_rows"] += 1
                continue
            keys = []
            if 2001 <= day.year <= 2020:
                keys.append((annual, (adm, day.year)))
            target = day.year + 2
            if target in windows and windows[target][0] <= day <= windows[target][1]:
                keys.append((recent, (adm, target)))
            for mapping, key in keys:
                acc = mapping.setdefault(key, [0.0] * len(values) + [0])
                for i, value in enumerate(values):
                    acc[i] += value
                acc[-1] += 1
    return annual, recent, dict(counts)


def validate_artifact_paths(paths: dict[str, Path], *, new: tuple[str, ...] = ()) -> None:
    """Reject canonical aliases and existing destinations before content access.

    resolve handles relative segments and symlinks, including dangling targets;
    inode identity additionally catches distinct hardlink names. Exclusive creates
    at the write boundaries prevent overwriting files created after this check.
    """
    resolved: dict[Path, str] = {}
    inodes: dict[tuple[int, int], str] = {}
    for role, path in paths.items():
        canonical = path.resolve()
        if canonical in resolved:
            raise ValueError(f"artifact paths must be distinct: {resolved[canonical]} and {role}")
        resolved[canonical] = role
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        inode = stat.st_dev, stat.st_ino
        if inode in inodes:
            raise ValueError(f"artifact paths must be distinct: {inodes[inode]} and {role}")
        inodes[inode] = role
    for role in new:
        if os.path.lexists(paths[role]):
            raise ValueError(f"artifact destination already exists: {role}: {paths[role]}")


def source_paths(data_root: Path) -> dict[str, Path]:
    folder = data_root / "maize" / "US"
    members = {k: folder / f"{k}_maize_US.csv" for k in ("yield", "soil", "location", "meteo")}
    return {
        **members,
        **{f"{k}_receipt": p.with_suffix(".manifest.json") for k, p in members.items()},
    }


def write_new_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def validate_cli_paths(args: argparse.Namespace) -> None:
    paths = {
        name: value
        for name in ("protocol", "prepared", "selection", "predictions", "output", "data_root")
        if (value := getattr(args, name)) is not None
    }
    if args.prepared is not None:
        paths["prepared_metadata"] = args.prepared.with_suffix(".json")
    if args.data_root is not None:
        paths.update(
            {f"source_{name}": path for name, path in source_paths(args.data_root).items()}
        )
    new = (
        ("prepared", "prepared_metadata")
        if args.command == "prepare"
        else (("predictions", "output") if args.command == "run" else ())
    )
    validate_artifact_paths(paths, new=new)


def prepare(data_root: Path, output: Path, protocol_hash: str) -> dict:
    validate_artifact_paths(
        {
            "prepared": output,
            "prepared_metadata": output.with_suffix(".json"),
            **{f"source_{name}": path for name, path in source_paths(data_root).items()},
        },
        new=("prepared", "prepared_metadata"),
    )
    folder = data_root / "maize" / "US"
    paths = {k: folder / f"{k}_maize_US.csv" for k in ("yield", "soil", "location", "meteo")}
    sources = {k: base.verify_member(p) for k, p in paths.items()}
    rows, label_counts = labels(paths["yield"])
    soils, locations = (
        base.read_unique_rows(paths["soil"]),
        base.read_unique_rows(paths["location"]),
    )
    annual, recent, counts = weather_counts(paths["meteo"], {r["adm_id"] for r in rows})
    eligible, zeros, excluded = Counter(), Counter(), defaultdict(Counter)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "year", "group", "yield", *base.FEATURES])
        for row in rows:
            adm, year = row["adm_id"], row["year"]
            try:
                static = [float((locations[adm] | soils[adm])[k]) for k in base.STATIC]
            except (KeyError, ValueError):
                excluded[year]["missing_static"] += 1
                continue
            if not all(math.isfinite(v) for v in static):
                excluded[year]["nonfinite_static"] += 1
                continue
            weather = base.make_weather_features(adm, year, annual, recent)
            if weather is None:
                excluded[year]["incomplete_weather"] += 1
                continue
            group = base.region_group("US", adm)
            writer.writerow([adm, year, group, row["yield"], year, *static, *weather])
            eligible[year] += 1
            zeros[year] += row["yield"] == 0
    metadata = {
        "implementation": identity(),
        "protocol_sha256": protocol_hash,
        "source_members": sources,
        "label_counts": label_counts,
        "weather_counts": counts,
        "eligible_by_year": dict(eligible),
        "zero_yields_by_year": dict(zeros),
        "excluded_by_year": dict(excluded),
        "features_sha256": base.file_digest(output)[0],
    }
    write_new_json(output.with_suffix(".json"), metadata)
    return metadata


@dataclass
class Panel:
    rows: list[dict]
    years: np.ndarray
    x: np.ndarray
    y: np.ndarray
    held_out: np.ndarray
    metadata: dict


def load_panel(path: Path) -> Panel:
    metadata = json.loads(path.with_suffix(".json").read_text())
    if (
        metadata["protocol_sha256"] != base.canonical_hash(PROTOCOL)
        or (metadata["features_sha256"] != base.file_digest(path)[0])
        or metadata["implementation"] != identity()
    ):
        raise ValueError("prepared input protocol/source/checksum mismatch")
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("empty prepared panel")
    years = np.array([int(r["year"]) for r in rows])
    x = np.array([[float(r[k]) for k in base.FEATURES] for r in rows])
    y = np.array([float(r["yield"]) for r in rows])
    if not np.isfinite(x).all() or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("nonfinite feature or invalid yield")
    if (years < 2008).any() or (years > 2023).any() or not np.array_equal(x[:, 0], years):
        raise ValueError("prepared feature year mismatch")
    keys = {(r["adm_id"], r["year"]) for r in rows}
    if len(keys) != len(rows) or any(
        base.region_group("US", r["adm_id"]) != r["group"] for r in rows
    ):
        raise ValueError("duplicate or incorrect administrative group")
    held = np.array([base.is_spatial_holdout("US", r["group"]) for r in rows])
    return Panel(rows, years, x, y, held, metadata)


def masks(panel: Panel, track: str, year: int, *, inner: bool) -> tuple[np.ndarray, np.ndarray]:
    allowed_years = PROTOCOL["inner_validation_years"] if inner else PROTOCOL["outer_test_years"]
    if track not in PROTOCOL["tracks"] or year not in allowed_years:
        raise ValueError("fold outside declared protocol")
    train = (panel.years >= 2008) & (panel.years <= year - 3)
    test = panel.years == year
    if track == "spatial_chronological":
        train &= ~panel.held_out
        test &= ~panel.held_out if inner else panel.held_out
    if train.sum() < 20 or test.sum() < 1:
        raise ValueError("insufficient rows for complete declared search")
    return train, test


def predict(
    candidate: dict, panel: Panel, train: np.ndarray, test: np.ndarray, year: int
) -> np.ndarray:
    family, params = candidate["family"], candidate["parameters"]
    if family in ("country_mean", "country_trend"):
        result = base.fit_predict(
            family, panel.x[train], panel.y[train], panel.years[train], panel.x[test], year
        )
    else:
        if family == "ridge":
            model = make_pipeline(StandardScaler(), Ridge(**params))
        else:
            cls = {
                "hist_gradient_boosting": HistGradientBoostingRegressor,
                "random_forest": RandomForestRegressor,
                "extra_trees": ExtraTreesRegressor,
            }[family]
            model = cls(**params)
        result = model.fit(panel.x[train], panel.y[train]).predict(panel.x[test])
    if len(result) != int(test.sum()) or not np.isfinite(result).all():
        raise ValueError("invalid candidate predictions; search aborted")
    return np.maximum(result, 0)


def fold_evidence(panel: Panel, track: str, year: int, train: np.ndarray, test: np.ndarray) -> dict:
    def groups(mask):
        return sorted({panel.rows[i]["group"] for i in np.flatnonzero(mask)})

    return {
        "track": track,
        "harvest_year": year,
        "issue_date": base.issue_date(year).isoformat(),
        "train_rows": int(train.sum()),
        "test_rows": int(test.sum()),
        "train_years": sorted(int(y) for y in np.unique(panel.years[train])),
        "train_groups": groups(train),
        "test_groups": groups(test),
    }


def winners(results: list[dict]) -> tuple[str, dict]:
    if [r["candidate_id"] for r in results] != [c["id"] for c in CANDIDATES]:
        raise ValueError("incomplete or reordered candidate scores")
    for row in results:
        if len(row["folds"]) != 4 or [f["harvest_year"] for f in row["folds"]] != [
            2014,
            2015,
            2016,
            2017,
        ]:
            raise ValueError("incomplete inner folds")
        value = float(np.mean([f["scores"]["rmse_t_ha"] for f in row["folds"]]))
        if not math.isfinite(value) or value < 0 or row["selection_rmse_t_ha"] != value:
            raise ValueError("invalid selection score")
    order = {c["id"]: i for i, c in enumerate(CANDIDATES)}

    def best(subset):
        return min(subset, key=lambda r: (r["selection_rmse_t_ha"], order[r["candidate_id"]]))[
            "candidate_id"
        ]

    families = {c["id"]: c["family"] for c in CANDIDATES}
    return best(results), {
        f: best([r for r in results if families[r["candidate_id"]] == f]) for f in FAMILIES
    }


def tune(panel: Panel) -> dict:
    tracks, fit_count = {}, 0
    for track in PROTOCOL["tracks"]:
        results = []
        for candidate in CANDIDATES:
            folds = []
            for year in PROTOCOL["inner_validation_years"]:
                train, test = masks(panel, track, year, inner=True)
                prediction = predict(candidate, panel, train, test, year)
                fit_count += 1
                folds.append(
                    fold_evidence(panel, track, year, train, test)
                    | {"scores": base.scores(panel.y[test], prediction)}
                )
            results.append(
                {
                    "candidate_id": candidate["id"],
                    "folds": folds,
                    "selection_rmse_t_ha": float(
                        np.mean([f["scores"]["rmse_t_ha"] for f in folds])
                    ),
                }
            )
        overall, per_family = winners(results)
        tracks[track] = {
            "candidates": results,
            "selected_overall": overall,
            "selected_per_family": per_family,
        }
    assert fit_count == PROTOCOL["bounds"]["inner_fits"]
    return {
        "protocol_sha256": base.canonical_hash(PROTOCOL),
        "implementation": identity(),
        "features_sha256": panel.metadata["features_sha256"],
        "inner_fit_count": fit_count,
        "tracks": tracks,
    }


def save_selection(path: Path, selection: dict) -> str:
    digest = base.canonical_hash(selection)
    write_once(path, {"selection": selection, "sha256": digest})
    return digest


def load_selection(path: Path, panel: Panel) -> tuple[dict, str]:
    frozen = json.loads(path.read_text())
    value = frozen["selection"]
    if (
        frozen["sha256"] != base.canonical_hash(value)
        or (value["protocol_sha256"] != base.canonical_hash(PROTOCOL))
        or value["implementation"] != identity()
        or (value["features_sha256"] != panel.metadata["features_sha256"])
        or value["inner_fit_count"] != 176
        or set(value["tracks"]) != set(PROTOCOL["tracks"])
    ):
        raise ValueError("frozen selection identity mismatch")
    for track in value["tracks"].values():
        overall, per_family = winners(track["candidates"])
        if track["selected_overall"] != overall or track["selected_per_family"] != per_family:
            raise ValueError("selection does not match inner scores")
    return value, frozen["sha256"]


def evaluate(panel: Panel, selection_path: Path, predictions_path: Path) -> dict:
    validate_artifact_paths(
        {"selection": selection_path, "predictions": predictions_path}, new=("predictions",)
    )
    selection, selection_hash = load_selection(selection_path, panel)
    by_id = {c["id"]: c for c in CANDIDATES}
    fits, results, pooled = 0, [], defaultdict(lambda: ([], []))
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with predictions_path.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "track",
                "candidate_id",
                "selected_overall",
                "adm_id",
                "harvest_year",
                "actual_t_ha",
                "predicted_t_ha",
            ]
        )
        for track in PROTOCOL["tracks"]:
            chosen = selection["tracks"][track]
            for year in PROTOCOL["outer_test_years"]:
                train, test = masks(panel, track, year, inner=False)
                for family in FAMILIES:
                    cid = chosen["selected_per_family"][family]
                    predicted = predict(by_id[cid], panel, train, test, year)
                    fits += 1
                    results.append(
                        fold_evidence(panel, track, year, train, test)
                        | {
                            "family": family,
                            "candidate_id": cid,
                            "selected_overall": cid == chosen["selected_overall"],
                            "scores": base.scores(panel.y[test], predicted),
                        }
                    )
                    pooled[track, cid][0].extend(panel.y[test].tolist())
                    pooled[track, cid][1].extend(predicted.tolist())
                    for index, value in zip(np.flatnonzero(test), predicted, strict=True):
                        writer.writerow(
                            [
                                track,
                                cid,
                                cid == chosen["selected_overall"],
                                panel.rows[index]["adm_id"],
                                year,
                                panel.y[index],
                                value,
                            ]
                        )
    assert fits == 36
    return {
        "selection_sha256": selection_hash,
        "selection": selection,
        "outer_fit_count": fits,
        "folds": results,
        "pooled_within_track": [
            {"track": t, "candidate_id": c, "scores": base.scores(np.array(y), np.array(p))}
            for (t, c), (y, p) in pooled.items()
        ],
        "predictions_sha256": base.file_digest(predictions_path)[0],
    }


def timeout_handler(_signum, _frame):
    raise TimeoutError("declared twenty-minute wall bound reached; experiment incomplete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "prepare", "run"))
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command != "freeze" and args.prepared is None:
        parser.error("--prepared is required")
    if args.command == "prepare" and args.data_root is None:
        parser.error("--data-root is required")
    if args.command == "run" and any(
        v is None for v in (args.selection, args.predictions, args.output)
    ):
        parser.error("--selection, --predictions and --output are required")
    validate_cli_paths(args)
    if args.command == "freeze":
        print(freeze(args.protocol))
        return 0
    digest = check_protocol(args.protocol)
    bound = PROTOCOL["bounds"]["address_space_bytes"]
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(PROTOCOL["bounds"]["wall_seconds"])
    started = time.monotonic()
    if args.command == "prepare":
        result = prepare(args.data_root, args.prepared, digest)
        print(json.dumps(result["eligible_by_year"], sort_keys=True))
        return 0
    panel = load_panel(args.prepared)
    with threadpool_limits(limits=2):
        selected = tune(panel)
        selection_hash = save_selection(args.selection, selected)
        inner_seconds = time.monotonic() - started
        print(
            f"selection frozen before outer evaluation: {selection_hash}; "
            f"inner elapsed seconds: {inner_seconds:.3f}",
            flush=True,
        )
        result = evaluate(panel, args.selection, args.predictions)
    report = {
        "protocol": PROTOCOL,
        "protocol_sha256": digest,
        "implementation": identity(),
        "coverage": panel.metadata,
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "sklearn_version": sklearn.__version__,
        **result,
        "inner_elapsed_seconds": inner_seconds,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    write_new_json(args.output, report)
    signal.alarm(0)
    print(
        f"completed {selected['inner_fit_count']} inner and {result['outer_fit_count']} outer fits",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
