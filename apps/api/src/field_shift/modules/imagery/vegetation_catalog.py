"""Bounded public STAC discovery. URLs from upstream are validated before GDAL sees them."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from math import isfinite
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from field_shift_science.fields.schemas import Polygon

from field_shift.modules.imagery.sentinel import ASSET_HOSTS, CATALOG, COLLECTION, _covers_field

MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 3


class VegetationUnavailableError(RuntimeError):
    public_message = "Sentinel-2 vegetation data is temporarily unavailable."


@dataclass(frozen=True)
class VegetationScene:
    id: str
    acquired_at: datetime
    epsg: int
    cloud: float
    red_url: str
    nir_url: str
    scl_url: str
    red_scale: float
    nir_scale: float
    spectral_assets: dict[str, tuple[str, float]] = field(default_factory=dict)

    @property
    def catalog_url(self) -> str:
        return f"{CATALOG}/collections/{COLLECTION}/items/{self.id}"


def _asset(item: dict, name: str, filename: str) -> tuple[str, float]:
    asset = item["assets"][name]
    href = asset["href"]
    url = urlsplit(href)
    if not (
        url.scheme == "https"
        and url.netloc in ASSET_HOSTS
        and not url.query
        and not url.fragment
        and re.fullmatch(r"/[A-Za-z0-9/_-]+/" + re.escape(filename), url.path)
    ):
        raise ValueError("Untrusted raster URL")
    scale = float(asset.get("raster:bands", [{}])[0].get("scale", 0.0001))
    if not isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("Invalid reflectance scale")
    return href, scale


def _candidate(item: dict, bbox: tuple, start: date, end: date) -> VegetationScene | None:
    try:
        props = item["properties"]
        identifier = item["id"]
        acquired = datetime.fromisoformat(props["datetime"].replace("Z", "+00:00"))
        epsg = int(props.get("proj:epsg") or props["proj:code"].removeprefix("EPSG:"))
        cloud = float(props["eo:cloud_cover"])
        baseline = float(props.get("s2:processing_baseline", "nan"))
        # Earth Search harmonizes COG pixels, but still publishes -0.1 in some asset
        # metadata. Do NOT apply that offset twice. Post-baseline-04 scenes without
        # explicit confirmation are ambiguous (upstream issues #66 and #71): skip.
        harmonized = props.get("earthsearch:boa_offset_applied") is True
        if not (
            item["collection"] == COLLECTION
            and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", identifier)
            and acquired.tzinfo is not None
            and start <= acquired.astimezone(UTC).date() <= end
            and (32601 <= epsg <= 32660 or 32701 <= epsg <= 32760)
            and isfinite(cloud)
            and 0 <= cloud <= 80
            and (harmonized or (isfinite(baseline) and 0 < baseline < 4))
            and _covers_field(item["geometry"], bbox)
        ):
            return None
        red, red_scale = _asset(item, "red", "B04.tif")
        nir, nir_scale = _asset(item, "nir", "B08.tif")
        scl, _ = _asset(item, "scl", "SCL.tif")
        additional = {}
        for name, filename in (
            ("green", "B03.tif"),
            ("rededge1", "B05.tif"),
            ("swir16", "B11.tif"),
        ):
            if name in item["assets"]:
                additional[name] = _asset(item, name, filename)
        return VegetationScene(
            identifier, acquired, epsg, cloud, red, nir, scl, red_scale, nir_scale, additional
        )
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OverflowError):
        return None


def search_month(geometry: Polygon, start: date, cutoff: date) -> list[VegetationScene]:
    x, y = zip(*geometry.coordinates[0], strict=True)
    bbox = min(x), min(y), max(x), max(y)
    query = urlencode(
        {
            "collections": COLLECTION,
            "bbox": ",".join(str(value) for value in bbox),
            "datetime": f"{start}T00:00:00Z/{cutoff}T23:59:59.999Z",
            "limit": 100,
            "sortby": "properties.eo:cloud_cover,-properties.datetime",
        }
    )
    try:
        request = Request(f"{CATALOG}/search?{query}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=8) as response:
            body = response.read(MAX_CATALOG_BYTES + 1)
        if len(body) > MAX_CATALOG_BYTES:
            raise ValueError("Catalog response exceeds bounded size")
        features = json.loads(body)["features"]
        if not isinstance(features, list) or len(features) > 100:
            raise ValueError("Invalid STAC features")
    except (URLError, OSError, ValueError, KeyError, TypeError):
        raise VegetationUnavailableError(VegetationUnavailableError.public_message) from None
    candidates = [
        candidate
        for item in features
        if (candidate := _candidate(item, bbox, start, cutoff)) is not None
    ]
    candidates.sort(key=lambda scene: (scene.cloud, -scene.acquired_at.timestamp()))
    # Adjacent MGRS tiles can duplicate an acquisition; do not waste the candidate budget.
    selected: dict[date, VegetationScene] = {}
    for candidate in candidates:
        selected.setdefault(candidate.acquired_at.date(), candidate)
    return list(selected.values())[:MAX_CANDIDATES]
