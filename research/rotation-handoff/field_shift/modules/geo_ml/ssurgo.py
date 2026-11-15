import json
from datetime import date
from functools import lru_cache
from math import isfinite
from time import monotonic
from typing import Annotated
from urllib.error import URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SDA_ENDPOINT = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
SSURGO_SOURCE = (
    "https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo"
)
_MAX_RESPONSE_BYTES = 32 * 1024
_CACHE_SECONDS = 24 * 60 * 60
_COLUMNS = (
    "mukey",
    "muname",
    "cokey",
    "compname",
    "comppct_r",
    "drainagecl",
    "hzdept_r",
    "hzdepb_r",
    "sandtotal_r",
    "silttotal_r",
    "claytotal_r",
    "om_r",
    "dbthirdbar_r",
    "wthirdbar_r",
    "wfifteenbar_r",
    "survey_date",
)

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class SoilSurveyError(RuntimeError):
    """USDA did not supply a valid soil survey response."""


class SoilProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    sand: Percent | None = None
    silt: Percent | None = None
    clay: Percent | None = None
    organic_matter: Percent | None = None
    bulk_density: float | None = Field(default=None, gt=0, le=3, allow_inf_nan=False)
    field_capacity: Fraction | None = None
    wilting_point: Fraction | None = None
    drainage: str | None = None
    mukey: str = Field(pattern=r"^\d+$")
    survey_date: date | None = None
    map_unit: str | None = None
    component_id: str | None = Field(default=None, pattern=r"^\d+$")
    component_name: str | None = None
    component_percent: Percent | None = None
    horizon_top_cm: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    horizon_bottom_cm: float | None = Field(default=None, gt=5, allow_inf_nan=False)


def _query(latitude: float, longitude: float) -> str:
    columns = ", ".join(
        ["m.mukey", "m.muname", "c.cokey", "c.compname", "c.comppct_r", "c.drainagecl"]
        + [f"h.{column}" for column in _COLUMNS[6:-1]]
        + ["CONVERT(varchar(10), sc.saverest, 23) AS survey_date"]
    )
    return f"""
        SELECT TOP 1 {columns}
        FROM SDA_Get_Mukey_from_intersection_with_WktWgs84(
            'POINT({longitude:.6f} {latitude:.6f})'
        ) p
        JOIN mapunit m ON m.mukey = p.mukey
        JOIN legend l ON l.lkey = m.lkey
        JOIN sacatalog sc ON sc.areasymbol = l.areasymbol
        JOIN component c ON c.mukey = m.mukey
        JOIN chorizon h ON h.cokey = c.cokey
        WHERE h.hzdept_r <= 5 AND h.hzdepb_r > 5 AND c.comppct_r > 0
        ORDER BY c.comppct_r DESC, m.mukey, c.cokey, h.chkey
    """


def _fraction(value: str | int | float | None) -> float | None:
    return float(value) / 100 if value is not None else None


def parse_soil_profile(data: dict, latitude: float, longitude: float) -> SoilProfile | None:
    try:
        if data == {}:
            return None
        table = data["Table"]
        if table == [] or table == [list(_COLUMNS)]:
            return None
        if len(table) != 2 or table[0] != list(_COLUMNS):
            raise ValueError("Unexpected tabular soil response")
        row = dict(zip(_COLUMNS, table[1], strict=True))
        return SoilProfile(
            latitude=latitude,
            longitude=longitude,
            sand=row["sandtotal_r"],
            silt=row["silttotal_r"],
            clay=row["claytotal_r"],
            organic_matter=row["om_r"],
            bulk_density=row["dbthirdbar_r"],
            field_capacity=_fraction(row["wthirdbar_r"]),
            wilting_point=_fraction(row["wfifteenbar_r"]),
            drainage=row["drainagecl"],
            mukey=row["mukey"],
            survey_date=row["survey_date"],
            map_unit=row["muname"],
            component_id=row["cokey"],
            component_name=row["compname"],
            component_percent=row["comppct_r"],
            horizon_top_cm=row["hzdept_r"],
            horizon_bottom_cm=row["hzdepb_r"],
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise SoilSurveyError("USDA returned an invalid soil survey profile.") from exc


def _fetch_profile(latitude: float, longitude: float) -> SoilProfile | None:
    payload = json.dumps(
        {"query": _query(latitude, longitude), "format": "JSON+COLUMNNAME"}
    ).encode()
    request = Request(
        SDA_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "FieldShift-Research/1.0"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise SoilSurveyError("USDA soil survey response exceeded its size bound.")
        document = json.loads(body)
        if not isinstance(document, dict):
            raise SoilSurveyError("USDA returned an invalid soil survey document.")
    except (URLError, TimeoutError, ValueError) as exc:
        raise SoilSurveyError("USDA Soil Data Access is temporarily unavailable.") from exc
    return parse_soil_profile(document, latitude, longitude)


@lru_cache(maxsize=512)
def _cached_profile(latitude: float, longitude: float, _epoch: int) -> SoilProfile | None:
    return _fetch_profile(latitude, longitude)


def fetch_soil_profile(latitude: float, longitude: float) -> SoilProfile | None:
    if not (
        isfinite(latitude)
        and isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        raise ValueError("Soil survey location requires valid WGS84 coordinates.")
    return _cached_profile(
        round(latitude, 6), round(longitude, 6), int(monotonic()) // _CACHE_SECONDS
    )
