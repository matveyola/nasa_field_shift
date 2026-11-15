"""Synthetic global scenario definitions, not surveyed farms or observed targets."""

from dataclasses import asdict, dataclass

from field_shift_science.fields.geometry import area_hectares
from field_shift_science.fields.schemas import FieldCreate, Polygon

VERSION = "global-tester-v1"


@dataclass(frozen=True)
class Case:
    id: str
    label: str
    latitude: float
    longitude: float
    crop: str
    invariant: str
    hypothesis: str

    def field(self, season: int) -> FieldCreate:
        # Small rectangles deliberately do not claim surveyed land or crop presence.
        x, y, delta = self.longitude, self.latitude, 0.001
        ring = [(x, y), (x + delta, y), (x + delta, y + delta), (x, y + delta), (x, y)]
        return FieldCreate(
            name=f"[SYNTHETIC {self.id}] {self.label}",
            crop=self.crop,
            area_ha=area_hectares(ring),
            season=season,
            geometry=Polygon(type="Polygon", coordinates=[ring]),
        )

    def manifest(self, season: int) -> dict:
        return {
            **asdict(self),
            "field": self.field(season).model_dump(mode="json"),
            "geometry_origin": "synthetic_fixture",
            "crop_origin": "hypothetical_user_choice_not_observed",
            "history": [],
            "history_status": "missing_not_invented",
            "soil_status": "missing",
            "land_evidence_status": "missing",
            "economics_status": "missing",
            "weather_status": "not_requested",
            "provider_receipts": [],
            "prompt": (
                f"For synthetic scenario {self.id} ({self.label}), explain what the current "
                f"field evidence can and cannot support for {self.crop}. Scenario hypothesis: "
                f"{self.hypothesis}. This is not measured evidence. {self.invariant} "
                "Use available tools only for this selected field. Cite actual sources and "
                "their dates; identify missing inputs. Do not invent profit, yield, weather, "
                "observations or completed tool calls. Ask before applying any change."
            ),
            "conversation_status": "planned_user_prompt_only",
            "expected_assistant_response": None,
        }


CASES = (
    Case(
        "G01",
        "Iowa soil-depth baseline",
        42.18,
        -93.75,
        "Maize",
        "Missing soil depth must remain unknown.",
        "Compare only after sourced soil exists.",
    ),
    Case(
        "G02",
        "Iowa neighboring polygon",
        42.182,
        -93.748,
        "Maize",
        "A shared weather grid cannot prove equal soil or yield.",
        "Neighbor of G01.",
    ),
    Case(
        "G03",
        "Kyiv reference",
        50.39,
        30.30,
        "Winter Wheat",
        "Regional reference calendars do not authorize planting.",
        "Unverified crop choice.",
    ),
    Case(
        "G04",
        "Kyiv neighboring polygon",
        50.392,
        30.302,
        "Sunflower",
        "Preserve field and boundary identities between neighbors.",
        "Neighbor of G03.",
    ),
    Case(
        "G05",
        "Rio urban location",
        -22.90,
        -43.20,
        "Soybean",
        "Coordinates or a calendar do not establish arable land.",
        "Land status unknown.",
    ),
    Case(
        "G06",
        "Rio regional neighbor",
        -22.5,
        -42.9,
        "Soybean",
        "Do not infer field suitability from nearby regional coverage.",
        "Land status unknown.",
    ),
    Case(
        "G07",
        "Antarctic cold case",
        -75.0,
        0.0,
        "Barley",
        "Do not crown low-demand or zero-growth crops as best; require evidence.",
        "Cold stress hypothesis, not a latitude blacklist.",
    ),
    Case(
        "G08",
        "Northern Finland",
        68.4,
        25.6,
        "Barley",
        "Thermal and maturity limits need actual dated inputs.",
        "Short-season hypothesis.",
    ),
    Case(
        "G09",
        "Arid Egypt without evidence of irrigation",
        26.8,
        29.9,
        "Maize",
        "Missing water resources must block claims of feasible irrigation.",
        "Irrigation unknown.",
    ),
    Case(
        "G10",
        "Arid Egypt irrigation comparison",
        26.8,
        29.9,
        "Maize",
        "A proposed irrigation scenario is not evidence of available water.",
        "Compare with G09 only after explicit management input; no water grant seeded.",
    ),
    Case(
        "G11",
        "Bangladesh rice hypothesis",
        23.8,
        90.4,
        "Rice",
        "Wet weather cannot substitute for supported paddy management.",
        "Paddy unverified.",
    ),
    Case(
        "G12",
        "Bangladesh managed-paddy comparison",
        23.8,
        90.4,
        "Rice",
        "Do not silently map rice to another supported crop.",
        "Proposed managed paddy; management and calendar evidence still missing.",
    ),
    Case(
        "G13",
        "Andean high altitude",
        -16.3,
        -68.1,
        "Potato",
        "Elevation and crop maturity must come from sources, not this case label.",
        "High-altitude stress hypothesis.",
    ),
    Case(
        "G14",
        "North Sea coast",
        52.9,
        4.75,
        "Barley",
        "Coastal weather-grid coverage does not prove usable soil or land.",
        "Coastal hypothesis.",
    ),
    Case(
        "G15",
        "Southern Australia",
        -34.9,
        138.6,
        "Barley",
        "Preserve southern-hemisphere calendar dates and harvest year.",
        "Season comparison.",
    ),
    Case(
        "G16",
        "Southern Australia cross-year cycle",
        -34.902,
        138.602,
        "Winter Wheat",
        "Never derive harvest year from sowing year alone.",
        "Cross-year cycle hypothesis.",
    ),
    Case(
        "G17",
        "Equatorial Uganda",
        0.3,
        32.6,
        "Maize",
        "Do not invent a single universal annual season at the equator.",
        "Multiple-cycle question.",
    ),
    Case(
        "G18",
        "Japan rice reference",
        36.4,
        140.1,
        "Rice",
        "Calendar availability is separate from modeled paddy capability.",
        "Rice hypothesis.",
    ),
    Case(
        "G19",
        "Canadian prairie",
        52.1,
        -106.6,
        "Rapeseed",
        "Unsupported cultivar or sourced calendar must remain explicit.",
        "Maturity hypothesis.",
    ),
    Case(
        "G20",
        "India heat case",
        26.9,
        75.8,
        "Maize",
        "Heat risk requires dated weather, not a country-specific ranking.",
        "Heat hypothesis.",
    ),
    Case(
        "G21",
        "Central Chile",
        -35.4,
        -71.7,
        "Maize",
        "Unknown irrigation and costs cannot become zero-cost profitability.",
        "Water-budget question.",
    ),
    Case(
        "G22",
        "Cape seasonal case",
        -33.9,
        18.5,
        "Barley",
        "Do not combine currencies or seasonal assumptions without explicit inputs.",
        "Economics/currency missing.",
    ),
    Case(
        "G23",
        "Kyiv unsupported crop",
        50.394,
        30.304,
        "Unknown fixture crop",
        "Unsupported crops must abstain rather than map to a familiar species.",
        "Unknown crop.",
    ),
    Case(
        "G24",
        "Iowa missing soil and economics",
        42.184,
        -93.746,
        "Peas",
        "Missing soil, costs and prices must remain null; pulses are not automatically peas.",
        "Missing-input case; no measured soil or financial values.",
    ),
)


def dataset(season: int) -> dict:
    return {
        "version": VERSION,
        "kind": "synthetic_global_application_test_plan",
        "season": season,
        "empirical_accuracy_claim": False,
        "production_model_special_cases": False,
        "cases": [case.manifest(season) for case in CASES],
        "provider_policy": {
            "fetch_on_seed": False,
            "weather_endpoint": "/api/fields/{field_id}/weather-summary",
            "required_receipt_fields": [
                "provider",
                "product",
                "retrieved_at",
                "available_at",
                "source_version",
                "input_geometry",
                "content_sha256",
                "units",
                "temporal_coverage",
            ],
            "rule": "Only actual returned evidence may populate receipts; absence stays missing.",
        },
    }
