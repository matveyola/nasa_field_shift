"""Independent, predecessor-matched validation at the Michigan State Living Field Lab."""

import csv
from collections import defaultdict
from hashlib import sha256
from itertools import dropwhile
from math import isfinite
from pathlib import Path
from statistics import fmean
from urllib.request import Request, urlopen

from field_shift_ml.fetch_observations import write_atomic
from field_shift_ml.train_rotation_yield import Sample

KBS_SOURCE = "https://lter.kbs.msu.edu/datatables/108"
KBS_EXPORT = KBS_SOURCE + ".csv"
KBS_SHA256 = "8cc824582dc976a70b03e5e75f4e1be3687d2c3ae7aead4f449bdaa00b4e0ca7"
KBS_SITE = "Kellogg Living Field Lab"
KBS_LOCATION = (42.410825, -85.374998)
KBS_COLUMNS = ("Year", "Plot", "Rep", "Cover", "Mgmnt", "Crop", "Harvest_date", "Yield")
CONTINUOUS_MAIZE = frozenset(("cont. corn", "corn (cont. corn)"))


def kbs_source_bytes(path: Path) -> bytes:
    cached = path.is_file()
    if cached:
        data = path.read_bytes()
    else:
        request = Request(KBS_EXPORT, headers={"User-Agent": "FieldShift-Offline-Research/1.0"})
        with urlopen(request, timeout=30) as response:
            data = response.read(400_001)
    if sha256(data).hexdigest() != KBS_SHA256:
        raise ValueError("The KBS yield export does not match the pinned trial release.")
    if not cached:
        write_atomic(path, data)
    return data


def yield_t_ha(value: str) -> float | None:
    try:
        numeric = float(value) / 1000
    except ValueError:
        return None
    if not isfinite(numeric) or not 0 <= numeric <= 25:
        return None
    return numeric


def kbs_contrasts(data: bytes) -> list[Sample]:
    lines = data.decode("utf-8-sig").splitlines()
    records = csv.DictReader(dropwhile(lambda line: line != ",".join(KBS_COLUMNS), lines))
    if records.fieldnames != list(KBS_COLUMNS):
        raise ValueError("The KBS export is missing the published trial columns.")

    plot_year = {}
    for row in records:
        if row["Mgmnt"] != "conventional" or row["Cover"] != "no cover":
            continue
        year = int(row["Year"])
        if 2006 <= year <= 2012:
            key = year, row["Plot"], row["Rep"]
            if key in plot_year:
                raise ValueError("The KBS export has a duplicate matched plot and year.")
            plot_year[key] = row

    control = {}
    rotation = {}
    for (year, plot, replicate), row in plot_year.items():
        previous = plot_year.get((year - 1, plot, replicate))
        value = yield_t_ha(row["Yield"])
        if previous is None or value is None:
            continue
        crop = row["Crop"].casefold()
        prior = previous["Crop"].casefold()
        if crop in CONTINUOUS_MAIZE and prior in CONTINUOUS_MAIZE:
            key = year, replicate
            if key in control:
                raise ValueError("Multiple continuous controls in one KBS replicate-year.")
            control[key] = value
        elif (crop, prior) in (("corn (c-s)", "soybean (c-s)"), ("corn (c-s-w)", "wheat (c-s-w)")):
            predecessor = "Soy" if crop == "corn (c-s)" else "Wheat"
            key = year, replicate, predecessor
            if key in rotation:
                raise ValueError("Multiple rotation yields in one KBS predecessor replicate-year.")
            rotation[key] = value

    effects = defaultdict(list)
    for (year, replicate, predecessor), value in rotation.items():
        if (year, replicate) in control:
            effects[year, predecessor].append(value - control[year, replicate])
    return [
        Sample(KBS_SITE, KBS_SITE, *KBS_LOCATION, year, predecessor, fmean(pairs), len(pairs))
        for (year, predecessor), pairs in sorted(effects.items())
        if len(pairs) >= 2
    ]
