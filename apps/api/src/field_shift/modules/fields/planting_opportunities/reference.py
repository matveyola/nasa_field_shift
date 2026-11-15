"""Read checksum-pinned package data only; no provider requests or runtime cache writes."""

import gzip
import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from io import BytesIO

from field_shift_science.crops.calendar import containing_cell

MAX_PACKED_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 32_000_000


@lru_cache(maxsize=1)
def load_reference():
    assets = files("field_shift_science.crops").joinpath("assets")
    receipt = json.loads(assets.joinpath("referencecropcalendar-v1.receipt.json").read_text())
    packed = assets.joinpath("referencecropcalendar-v1.json.gz").read_bytes()
    if (
        len(packed) > MAX_PACKED_BYTES
        or hashlib.sha256(packed).hexdigest() != receipt["artifact_sha256"]
    ):
        raise ValueError("Calendar reference checksum or size mismatch")
    with gzip.GzipFile(fileobj=BytesIO(packed)) as stream:
        raw = stream.read(MAX_EXPANDED_BYTES + 1)
    if len(raw) > MAX_EXPANDED_BYTES or len(raw) != receipt["expanded_bytes"]:
        raise ValueError("Calendar expansion bound mismatch")
    data = json.loads(raw)
    if data["version"] != receipt["version"] or len(data["calendars"]) != 7:
        raise ValueError("Unsupported calendar artifact version/scope")
    if len(data["latitude"]) != 2160 or len(data["longitude"]) != 4320:
        raise ValueError("Calendar coordinate shape mismatch")
    return data, receipt


def reference_at(latitude, longitude):
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Coordinates outside WGS84")
    data, receipt = load_reference()
    row = containing_cell(data["latitude"], latitude)
    column = containing_cell(data["longitude"], longitude)
    if row is None or column is None:
        return [], None, receipt
    values = []
    for calendar in data["calendars"]:
        planting = maturity = None
        for start, end, sow, harvest in calendar["rows"][row]:
            if start <= column < end:
                planting, maturity = sow or None, harvest or None
                break
        values.append(
            {
                "crop_id": calendar["crop_id"],
                "cycle_id": calendar["cycle_id"],
                "planting_month": planting,
                "maturity_month": maturity,
            }
        )
    cell = {
        "latitude": data["latitude"][row],
        "longitude": data["longitude"][column],
        "row": row,
        "column": column,
    }
    return values, cell, receipt
