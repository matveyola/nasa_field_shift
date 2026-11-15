"""Infer the statistical area from packaged Natural Earth country polygons."""

from functools import lru_cache
from importlib.resources import files
from itertools import pairwise
from json import loads


@lru_cache(maxsize=1)
def _boundaries() -> dict:
    resource = (
        files(__package__).joinpath("artifacts").joinpath("country-boundaries.json")
    )
    return loads(resource.read_text(encoding="utf-8"))


def _contains(ring: list, longitude: float, latitude: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in pairwise(ring):
        if (y1 > latitude) != (y2 > latitude):
            crossing = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing:
                inside = not inside
    return inside


def country_at(latitude: float, longitude: float) -> str | None:
    matches = set()
    for code, polygons in _boundaries()["countries"].items():
        for (west, south, east, north), (exterior, *holes) in polygons:
            if not (west <= longitude <= east and south <= latitude <= north):
                continue
            if _contains(exterior, longitude, latitude) and not any(
                _contains(hole, longitude, latitude) for hole in holes
            ):
                matches.add(code)
                break
    return matches.pop() if len(matches) == 1 else None
