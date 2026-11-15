"""Train a maize rotation yield contrast using matched USDA trial observations."""

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from statistics import fmean
from urllib.request import urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
from field_shift_science.rotations.yield_model import (
    FEATURES,
    SOURCE_URL,
    Predecessor,
    RotationYieldModel,
    artifact_version,
    effect_features,
    site_distance,
)
from sklearn.linear_model import Ridge

from field_shift_ml.train_moisture import evaluate_predictions, write_atomically

WORKBOOK_URL = "https://ndownloader.figshare.com/files/44334239"
WORKBOOK_SHA256 = "b9e40bf74554c32849dcb6e6a613d6b7a60b4b8a2f8cbed33073d7c7732f95b6"
TEST_FARMS = frozenset(("HOYTVILLE.LTR", "MAR", "ORR", "SERF"))
CALIBRATION_FARMS = frozenset(("FREEMAN", "LAN"))
PREDECESSORS = ("Soy", "Wheat")
MAIZE_TREATMENTS = frozenset(("ROT4", "ROT5", "ROT6", "ROT7", "ROT8", "ROT18", "ROT19"))
THREE_CROP_TREATMENTS = frozenset(("ROT6", "ROT7", "ROT8", "ROT18", "ROT19"))
YEARS = range(2012, 2016)
FARM_ALIAS = {"WOOSTER.COV": "WOOSTER", "WOOSTER.LTR": "WOOSTER"}


@dataclass(frozen=True, slots=True)
class TrialPlan:
    no_till_share: float
    drained_share: float
    three_crop_share: float


@dataclass(frozen=True, slots=True)
class Sample:
    site: str
    farm: str
    latitude: float
    longitude: float
    year: int
    preceding_crop: Predecessor
    delta_t_ha: float
    management_pairs: int
    plan: TrialPlan | None = None


def workbook_bytes(path: Path) -> bytes:
    if path.is_file():
        return path.read_bytes()
    with urlopen(WORKBOOK_URL, timeout=40) as response:
        data = response.read(13_000_001)
    verify_workbook(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def verify_workbook(data: bytes) -> None:
    if sha256(data).hexdigest() != WORKBOOK_SHA256:
        raise ValueError("The USDA workbook does not match the pinned research release.")


def worksheet(workbook: ZipFile, strings: list[str], sheet_number: int) -> Iterator[dict[str, str]]:
    with workbook.open(f"xl/worksheets/sheet{sheet_number}.xml") as stream:
        for _, element in ElementTree.iterparse(stream, events=("end",)):
            if not element.tag.endswith("}row"):
                continue
            row = {}
            for cell in element:
                value = cell.find("{*}v")
                if value is None or not value.text:
                    continue
                column = re.sub(r"\d+$", "", cell.attrib["r"])
                row[column] = strings[int(value.text)] if cell.get("t") == "s" else value.text
            element.clear()
            yield row


def matched_yields(path: Path) -> list[Sample]:
    verify_workbook(workbook_bytes(path))
    with ZipFile(path) as workbook:
        strings = [
            "".join(row.itertext()).strip()
            for row in ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
        ]
        plot_sheet = worksheet(workbook, strings, 1)
        yield_sheet = worksheet(workbook, strings, 2)
        site_sheet = worksheet(workbook, strings, 11)
        if (
            next(plot_sheet)["E"] != "rotation"
            or next(yield_sheet)["S"] != "AGR17"
            or next(site_sheet)["C"] != "NW Lat"
        ):
            raise ValueError("The USDA workbook schema does not match the documented release.")
        next(plot_sheet)
        next(plot_sheet)
        next(yield_sheet)
        if next(yield_sheet)["S"] != "kg ha-1":
            raise ValueError("Expected maize yield in kilograms per hectare.")
        next(site_sheet)
        next(site_sheet)
        plots = {(row["A"], row["C"]): row for row in plot_sheet if row.get("C")}
        sites = {
            row["A"]: (
                fmean(float(row[col]) for col in "CEGI"),
                fmean(float(row[col]) for col in "BDFH"),
            )
            for row in site_sheet
            if all(row.get(column) and row[column] != "n/a" for column in "ABCDEFGHI")
        }
        buckets = defaultdict(lambda: defaultdict(list))
        used = set()
        for row in yield_sheet:
            if not row.get("S") or not row.get("C"):
                continue
            year = int(row["C"])
            site, plot_id = row["A"], row["B"]
            if year not in YEARS or site not in sites or (site, plot_id) not in plots:
                continue
            plot = plots[site, plot_id]
            treatment = plot["E"]
            if (
                plot.get("F") == "DWM5"
                or plot.get("G") != "NIT2"
                or plot.get("IJKLM"[year - 2011]) != "Corn"
                or (treatment != "ROT1" and treatment not in MAIZE_TREATMENTS)
            ):
                continue
            previous = {"Corn": "Maize", "Soybean": "Soy", "Wheat": "Wheat"}.get(
                plot.get("IJKLM"[year - 2012])
            )
            if (treatment == "ROT1" and previous != "Maize") or (
                treatment != "ROT1" and previous not in PREDECESSORS
            ):
                continue
            try:
                value = float(row["S"]) / 1000
            except ValueError:
                continue
            if not 0 <= value <= 25:
                continue
            key = (site, plot_id, year)
            if key in used:
                raise ValueError("The workbook contains duplicate maize plot-year yields.")
            used.add(key)
            stratum = (site, year, plot.get("D"), plot.get("F"), plot.get("G"), plot.get("H"))
            buckets[stratum][previous].append((value, treatment))
    contrasts = defaultdict(list)
    for (site, year, tillage, drainage, *_), yields in buckets.items():
        if not yields.get("Maize"):
            continue
        control = fmean(value for value, _ in yields["Maize"])
        for previous in PREDECESSORS:
            if yields.get(previous):
                rotated = yields[previous]
                contrast = fmean(value for value, _ in rotated) - control
                three_crop = fmean(treatment in THREE_CROP_TREATMENTS for _, treatment in rotated)
                contrasts[site, year, previous].append((contrast, tillage, drainage, three_crop))
    return [
        Sample(
            site,
            FARM_ALIAS.get(site, site),
            *sites[site],
            year,
            previous,
            fmean(value for value, *_ in rows),
            len(rows),
            (
                TrialPlan(
                    fmean(tillage == "TIL1" for _, tillage, _, _ in rows),
                    fmean(drainage in ("DWM2", "DWM3", "DWM4") for _, _, drainage, _ in rows),
                    fmean(three_crop for *_, three_crop in rows),
                )
                if all(
                    tillage in ("TIL1", "TIL2") and drainage in ("DWM1", "DWM2", "DWM3", "DWM4")
                    for _, tillage, drainage, _ in rows
                )
                else None
            ),
        )
        for (site, year, previous), rows in sorted(contrasts.items())
    ]


def farm_weights(samples: list[Sample]) -> np.ndarray:
    counts = Counter(sample.farm for sample in samples)
    return np.array([1 / counts[sample.farm] for sample in samples])


def model_from(payload: dict) -> RotationYieldModel:
    return RotationYieldModel.model_validate({"version": artifact_version(payload), **payload})


def check_independent(left: list[Sample], right: list[Sample]) -> float:
    minimum = min(
        site_distance(a.latitude, a.longitude, b.latitude, b.longitude) for a in left for b in right
    )
    if minimum < 50:
        raise ValueError(f"Independent trial groups are only {minimum:.1f} km apart.")
    return minimum


def benchmark(model: RotationYieldModel, samples: list[Sample], baseline: dict[str, float]) -> dict:
    target = np.array([row.delta_t_ha for row in samples])
    predictions = np.array(
        [model.predict(row.latitude, row.longitude, row.preceding_crop) for row in samples]
    )
    weights = farm_weights(samples)
    return {
        "candidate": evaluate_predictions(predictions, target, weights),
        "baseline": evaluate_predictions(
            np.array([baseline[row.preceding_crop] for row in samples]), target, weights
        ),
        "farm_years": len(samples),
        "farms": sorted({row.farm for row in samples}),
        "interval_coverage": float(
            np.average(abs(target - predictions) <= model.interval_half_width, weights=weights)
        ),
    }


def train(samples: list[Sample]) -> RotationYieldModel:
    fit = [row for row in samples if row.farm not in CALIBRATION_FARMS | TEST_FARMS]
    calibration = [row for row in samples if row.farm in CALIBRATION_FARMS]
    test = [row for row in samples if row.farm in TEST_FARMS]
    distance = min(
        check_independent(fit, calibration),
        check_independent(fit, test),
        check_independent(calibration, test),
    )
    x = [effect_features(row.latitude, row.longitude, row.preceding_crop) for row in fit]
    y = [row.delta_t_ha for row in fit]
    fitted = Ridge(alpha=6).fit(
        x, y, sample_weight=farm_weights(fit) * len(fit) / len({row.farm for row in fit})
    )
    baseline = {}
    for previous in PREDECESSORS:
        rows = [row for row in fit if row.preceding_crop == previous]
        baseline[previous] = float(
            np.average([row.delta_t_ha for row in rows], weights=farm_weights(rows))
        )
    payload = {
        "schema_version": 1,
        "feature_names": list(FEATURES),
        "intercept": float(fitted.intercept_),
        "weights": fitted.coef_.tolist(),
        "interval_half_width": 1.0,
        "sites_by_predecessor": {
            previous: [
                list(site)
                for site in sorted(
                    {
                        (row.latitude, row.longitude)
                        for row in samples
                        if row.preceding_crop == previous
                    }
                )
            ]
            for previous in PREDECESSORS
        },
        "promoted": False,
        "validation": {},
    }
    candidate = model_from(payload)
    errors = np.array(
        [
            abs(row.delta_t_ha - candidate.predict(row.latitude, row.longitude, row.preceding_crop))
            for row in calibration
        ]
    )
    order = np.argsort(errors)
    weights = farm_weights(calibration)
    index = np.searchsorted(np.cumsum(weights[order]), 0.9 * weights.sum())
    payload["interval_half_width"] = float(max(0.001, errors[order[index]]))
    candidate = model_from(payload)
    overall = benchmark(candidate, test, baseline)
    transitions = {
        previous: benchmark(
            candidate, [row for row in test if row.preceding_crop == previous], baseline
        )
        for previous in PREDECESSORS
    }
    checks = {
        "site_coverage": len(overall["farms"]) >= 3
        and all(
            len(result["farms"]) >= 2 and result["farm_years"] >= 6
            for result in transitions.values()
        ),
        "mae_improvement": overall["candidate"]["mae"] <= 0.95 * overall["baseline"]["mae"],
        "rmse_improvement": overall["candidate"]["rmse"] <= 0.95 * overall["baseline"]["rmse"],
        "transition_stability": all(
            result["candidate"]["rmse"] <= 1.1 * result["baseline"]["rmse"]
            for result in transitions.values()
        ),
        "coverage": overall["interval_coverage"] >= 0.7,
    }
    payload["promoted"] = all(checks.values())
    payload["validation"] = {
        "source_url": SOURCE_URL,
        "source_sha256": WORKBOOK_SHA256,
        "matched_site_years": len(samples),
        "matched_management_pairs": sum(row.management_pairs for row in samples),
        "training_sites": len({row.farm for row in fit}),
        "calibration_sites": len({row.farm for row in calibration}),
        "test_sites": len({row.farm for row in test}),
        "minimum_split_distance_km": distance,
        "fitting_farms": sorted({row.farm for row in fit}),
        "calibration_farms": sorted({row.farm for row in calibration}),
        "test_farms": sorted({row.farm for row in test}),
        "baseline_t_ha": baseline,
        "evaluation": overall,
        "by_predecessor": transitions,
        "by_farm": {
            farm: benchmark(candidate, [row for row in test if row.farm == farm], baseline)
            for farm in overall["farms"]
        },
        "gate": checks,
    }
    return model_from(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = train(matched_yields(args.workbook))
    write_atomically(args.output, model.model_dump_json(indent=2) + "\n")
    print(json.dumps({"version": model.version, "promoted": model.promoted, **model.validation}))


if __name__ == "__main__":
    main()
