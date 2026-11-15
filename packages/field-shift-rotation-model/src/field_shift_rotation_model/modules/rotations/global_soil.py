"""Fetch estimated soil hydraulic layers from the public ISRIC SoilGrids WCS."""

import ssl
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException
from math import isfinite
from struct import Struct
from struct import error as StructError
from urllib.parse import urlencode
from urllib.request import Request

import certifi

from field_shift_rotation_model.modules.rotations.historical_fields import SoilLayer
from field_shift_rotation_model.network import urlopen

SOILGRIDS_SOURCE_URL = "https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html"
SOILGRIDS_WCS = "https://maps.isric.org/mapserv"
CRS_4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"
DEPTHS_CM = ((0, 5), (5, 15), (15, 30), (30, 60), (60, 100))
WATER_RETENTION_PROPERTIES = ("wv0033", "wv1500")
HALF_WINDOW_DEGREES = 0.001
MAX_RESPONSE_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 10
WCS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="soilgrids")


class SoilProviderError(RuntimeError):
    """ISRIC could not supply a physically valid point profile."""


@dataclass(frozen=True, slots=True)
class SoilProfile:
    layers: tuple[SoilLayer, ...]
    source_url: str = SOILGRIDS_SOURCE_URL


def coverage_url(
    property_id: str, depth: tuple[int, int], latitude: float, longitude: float
) -> str:
    top, bottom = depth
    west = max(-180, longitude - HALF_WINDOW_DEGREES)
    east = min(180, longitude + HALF_WINDOW_DEGREES)
    south = max(-90, latitude - HALF_WINDOW_DEGREES)
    north = min(90, latitude + HALF_WINDOW_DEGREES)
    params = [
        ("SERVICE", "WCS"),
        ("VERSION", "2.0.1"),
        ("REQUEST", "GetCoverage"),
        ("COVERAGEID", f"{property_id}_{top}-{bottom}cm_mean"),
        ("FORMAT", "GEOTIFF_INT16"),
        ("SUBSET", f"X({west:.7f},{east:.7f})"),
        ("SUBSET", f"Y({south:.7f},{north:.7f})"),
        ("SUBSETTINGCRS", CRS_4326),
        ("OUTPUTCRS", CRS_4326),
        ("SCALESIZE", "x(1),y(1)"),
    ]
    return f"{SOILGRIDS_WCS}/{property_id}?{urlencode(params)}"


def read_one_int16_pixel(data: bytes) -> int:
    """Read the single GeoTIFF sample requested from SoilGrids GDAL/MapServer."""
    endian = {b"II": "<", b"MM": ">"}.get(data[:2])
    if endian is None:
        raise ValueError("expected a TIFF response")

    ushort = Struct(f"{endian}H")
    ulong = Struct(f"{endian}I")
    entry_header = Struct(f"{endian}HHI")
    if ushort.unpack_from(data, 2)[0] != 42:
        raise ValueError("unsupported TIFF signature")
    directory = ulong.unpack_from(data, 4)[0]

    # For one tile/strip the needed directory values are all inline SHORT or LONG.
    tags = {}
    count = ushort.unpack_from(data, directory)[0]
    for offset in range(directory + 2, directory + 2 + 12 * count, 12):
        tag, typ, length = entry_header.unpack_from(data, offset)
        if length == 1 and typ in (3, 4):
            decoder = ushort if typ == 3 else ulong
            tags[tag] = decoder.unpack_from(data, offset + 8)[0]

    if (
        tags.get(256) != 1
        or tags.get(257) != 1
        or tags.get(258) != 16
        or tags.get(277, 1) != 1
        or tags.get(339) != 2
        or tags.get(317, 1) not in (1, 2)
    ):
        raise ValueError("expected a single signed 16-bit TIFF pixel")

    start = tags.get(324, tags.get(273))
    nbytes = tags.get(325, tags.get(279))
    if start is None or not nbytes or start + nbytes > len(data):
        raise ValueError("invalid TIFF tile or strip")
    encoded = data[start : start + nbytes]

    compression = tags.get(259, 1)
    if compression in (8, 32946):
        # Decode only the first sample. The optional horizontal predictor
        # leaves that sample unchanged, even if the rest of the tile is padded.
        pixel = zlib.decompressobj().decompress(encoded, 2)
    elif compression == 1:
        pixel = encoded[:2]
    else:
        raise ValueError("unsupported TIFF compression")
    return Struct(f"{endian}h").unpack(pixel)[0]


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def sample_soilgrids_pixel(url: str) -> int:
    """Read one nonnegative raw sample for a SoilGrids property."""
    request = Request(url, headers={"User-Agent": "FieldShift SoilGrids adapter/1.0"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=_ssl_context()) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("ISRIC returned an oversized point response")
    sample = read_one_int16_pixel(data)
    if sample < 0:
        raise ValueError("no SoilGrids value at this coordinate")
    return sample


def _sample_coverage(url: str) -> float:
    sample = sample_soilgrids_pixel(url)
    if sample > 1000:
        raise ValueError("invalid SoilGrids volumetric water content")
    # ISRIC stores water content in 10^-3 m³/m³.
    return sample / 1000


def validate_soil_coordinates(latitude: float, longitude: float) -> None:
    if not (
        isfinite(latitude)
        and -90 <= latitude <= 90
        and isfinite(longitude)
        and -180 <= longitude <= 180
    ):
        raise ValueError("Expected valid latitude and longitude in degrees.")


@lru_cache(maxsize=128)
def fetch_soil_profile(latitude: float, longitude: float) -> SoilProfile:
    validate_soil_coordinates(latitude, longitude)
    urls = [
        coverage_url(property_id, depth, latitude, longitude)
        for depth in DEPTHS_CM
        for property_id in WATER_RETENTION_PROPERTIES
    ]
    samples = [WCS_POOL.submit(_sample_coverage, url) for url in urls]
    try:
        layers = tuple(
            SoilLayer(
                top_m=top / 100,
                bottom_m=bottom / 100,
                field_capacity=samples[index * 2].result(),
                wilting_point=samples[index * 2 + 1].result(),
            )
            for index, (top, bottom) in enumerate(DEPTHS_CM)
        )
    except (OSError, HTTPException, StructError, ValueError, zlib.error) as error:
        for sample in samples:
            sample.cancel()
        raise SoilProviderError(
            "ISRIC SoilGrids could not supply all five water-retention layers "
            "at these coordinates. Use measured field layers or retry."
        ) from error
    return SoilProfile(layers=layers)
