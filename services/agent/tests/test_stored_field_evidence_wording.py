"""How the host's stored ERA5-Land surface moisture and terrain facts read to a farmer (2026-11-14).

The facts are shaped as apps/api/src/field_shift/modules/agent/field_context.py sends them. ERA5-Land is a coarse
weather-model estimate: it must never read as satellite data, a field measurement or water at the roots. Heights are
metres, written "м" in Ukrainian.
"""

import json
import re
from pathlib import Path

import pytest
from agent_service import plain
from agent_service.dev_agent import _fact_line
from agent_service.grounding import format_quantity, render_fact
from contracts.models import Fact, Quantity

RETRIEVED = "2026-11-10T12:00:00+00:00"
MOISTURE = {
    "id": "f_surface_soil_moisture",
    "domain": "soil",
    "label_key": "fact.surface_soil_moisture",
    "quantity": {"value": 0.285, "unit": "m3/m3"},
    "depth_cm": [0, 7],
    "provenance": {
        "kind": "weather_data",
        "data_status": "cached",
        "source": {
            "provider": "Copernicus Climate Change Service via Open-Meteo",
            "product_id": "ERA5-Land soil_moisture_0_to_7cm",
            "version": "unverified",
            "url": "https://doi.org/10.24381/cds.e2161bac",
        },
        "method": "ERA5-Land weather-model estimate (reanalysis), not a satellite or on-field measurement: daily mean "
        "of 24 hourly values for the 0-7 cm layer in the nearest 0.1 degree grid cell (about 11 km); 7 days "
        "old. One value for the whole area, not root-zone water.",
        "temporal": {
            "start": "2026-11-03",
            "end": "2026-11-03",
            "aggregation": "daily_mean",
        },
        "spatial": {"resolution_m": 11100.0},
        "retrieved_at": RETRIEVED,
    },
}
HIGHEST = {
    "id": "f_elevation_max",
    "domain": "terrain",
    "label_key": "fact.elevation_max",
    "quantity": {"value": 153, "unit": "m"},
    "provenance": {
        "kind": "deterministic_calc",
        "data_status": "cached",
        "source": {
            "provider": "Copernicus",
            "product_id": "Copernicus DEM GLO-30",
            "version": "2021 AWS release",
        },
        "method": "Lowest and highest 30 m elevation cells inside the current boundary (98.5% covered).",
        "spatial": {"resolution_m": 30},
        "retrieved_at": RETRIEVED,
    },
}


def fact(data: dict) -> Fact:
    return Fact.model_validate_json(
        json.dumps(data)
    )  # strict contract, parsed as the agent parses requests


@pytest.mark.parametrize(("lang", "text"), [("uk", "141 м"), ("en", "141 m")])
def test_metres_are_written_in_the_answers_language(lang, text):
    assert format_quantity(Quantity(value=141, unit="m"), lang) == text
    assert (
        plain.unit("m3/m3", "uk") == " м³/м³" and plain.unit("mm", "en") == " mm"
    )  # neighbours unchanged


def test_a_cited_height_reads_in_ukrainian_units():
    assert render_fact(fact(HIGHEST), "uk").startswith("**153 м**")
    assert render_fact(fact(HIGHEST), "en").startswith("**153 m**")


@pytest.mark.parametrize("lang", ["uk", "en"])
def test_surface_moisture_reads_as_a_model_estimate_not_satellite_or_root_zone(lang):
    line = _fact_line(fact(MOISTURE), lang)
    if lang == "uk":
        assert (
            "Вологість верхнього шару ґрунту (0–7 см, оцінка погодної моделі)" in line
        )
        assert (
            "оцінка погодної моделі ERA5-Land, не супутник і не заміри на полі" in line
        )
        assert "біля коренів" not in line
    else:
        assert "Topsoil moisture (0–7 cm, weather-model estimate)" in line
        assert "not a satellite or field measurement" in line
        assert "root" not in line
    assert (
        "weather_data" not in line and "soil_moisture_0_to_7cm" not in line
    )  # no raw ids for the farmer


def test_terrain_lines_name_the_points_and_the_map_in_plain_words():
    line = _fact_line(fact(HIGHEST), "uk")
    assert (
        line.startswith("- **Найвища точка поля**: 153 м")
        and "Copernicus DEM GLO-30" not in line
    )
    assert plain.source("Copernicus DEM GLO-30", "uk") == "карта рельєфу Copernicus"


def test_the_widget_names_the_new_facts_as_the_agent_does():
    strings = (
        Path(__file__).resolve().parents[1] / "frontend/src/widget/i18n/strings.ts"
    ).read_text(encoding="utf-8")
    for key in (
        "fact.elevation_min",
        "fact.elevation_max",
        "fact.surface_soil_moisture",
    ):
        labels = re.findall(rf'"{re.escape(key)}": "([^"]+)"', strings)
        assert [label.lower() for label in labels] == [
            plain.NAMES["en"][key],
            plain.NAMES["uk"][key],
        ], key
