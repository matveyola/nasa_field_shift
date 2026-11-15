"""Original handoff tests, adapted only for installed namespace/resources."""

import asyncio
import json
import os
from importlib.resources import files
from unittest import TestCase
from unittest.mock import patch

from field_shift_rotation_model.app import app

EXAMPLES = files("field_shift_rotation_model").joinpath("examples/climate/api")
SUTHERLAND_2027_ROUTE = "/api/rotations/scenarios/sutherland-2027"


async def request(route, payload=None):
    path, _, query = route.partition("?")
    body = json.dumps(payload).encode() if payload is not None else b""
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET" if payload is None else "POST",
            "scheme": "http",
            "path": path,
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("localhost", 8081),
        },
        receive,
        send,
    )
    return messages[0]["status"], json.loads(
        b"".join(message["body"] for message in messages if message["type"] == "http.response.body")
    )


def example(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


class ClimateAPITests(TestCase):
    def call(self, route, payload=None, status=200):
        got_status, response = asyncio.run(request(f"/api/rotations/climate/{route}", payload))
        self.assertEqual(got_status, status, response)
        return response

    def test_catalog_and_scope(self):
        catalog = self.call("catalog")
        self.assertIn("revenue", catalog["priorities"])
        self.assertIn("revenue_after_seed", catalog["priorities"])
        self.assertNotIn("ebitda", catalog["priorities"])
        self.assertIn("sutherland_nw", {field["id"] for field in catalog["fields"]})
        self.call(
            "step", example("sutherland-step-1") | {"planting_date": "2050-05-01"}, status=422
        )
        with patch.dict(os.environ, {"FIELDSHIFT_RUSLAN_DIR": ""}):
            self.assertTrue(self.call("catalog")["model_configured"])
        with patch.dict(os.environ, {"FIELDSHIFT_RUSLAN_DIR": str(EXAMPLES / "missing-engine")}):
            self.call("step", example("sutherland-step-1"), status=503)

    def test_cultivar_references_use_trial_region(self):
        route = "/api/rotations/cultivar-references"
        status, rows = asyncio.run(request(route))
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 20)
        status, rows = asyncio.run(request(route + "?crop_id=soybeans&region_code=us-ia"))
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["kind"] == "cultivar" for row in rows))
        self.assertTrue(all(row["trial_location"] == "Sutherland, Iowa" for row in rows))
        status, rows = asyncio.run(request(route + "?crop_id=sunflower&region_code=US-IA"))
        self.assertEqual(status, 200)
        self.assertEqual(rows, [])
        status, rows = asyncio.run(request(route + "?crop_id=sunflower&region_code=US-ND"))
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["kind"] == "hybrid" for row in rows))

    def test_sutherland_2027_requires_configured_engine(self):
        with patch.dict(os.environ, {"FIELDSHIFT_RUSLAN_DIR": str(EXAMPLES / "missing-engine")}):
            status, response = asyncio.run(request(SUTHERLAND_2027_ROUTE, {}))
        self.assertEqual(status, 503)
        self.assertIn("FIELDSHIFT_RUSLAN_DIR", response["detail"])

    def test_sutherland_2027_replays_bundled_inputs(self):
        fixture = EXAMPLES.parents[1] / "sutherland-2026-28/result-nasa-2027.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))
        with patch(
            "field_shift_rotation_model.modules.rotations.climate_reference.urlopen",
            side_effect=AssertionError("Sutherland should use local data"),
        ):
            status, result = asyncio.run(request(SUTHERLAND_2027_ROUTE, {}))
        self.assertEqual(status, 200, result)
        self.assertEqual(result, expected)

    def test_three_seasons_and_paired_rotations(self):
        responses = []
        for number in (1, 2, 3):
            outcome = self.call("step", example(f"sutherland-step-{number}"))
            self.assertEqual(outcome["step"], number)
            self.assertEqual(outcome["weather_delivery"], "bundled_snapshot")
            self.assertEqual(len(outcome["top_candidates"]), 3)
            self.assertFalse(outcome["water_stress_comparable"])
            self.assertIsNotNone(
                outcome["top_candidates"][0]["agronomy"]["candidate"]["national_nutrient_reference"]
            )
            responses.append(outcome)
        self.assertFalse(responses[-1]["has_next_step_after_selection"])
        self.assertTrue(
            any(len(v) == 2 for v in responses[-1]["selected_water_by_scenario"].values())
        )
        plan = self.call("compare", example("sutherland-compare"))["plans"][0]
        for idx, selected in enumerate(("maize_grain", "sunflower", "soybeans")):
            alternative = next(
                row for row in responses[idx]["candidates"] if row["crop_id"] == selected
            )
            self.assertEqual(alternative["scenarios"], plan["seasons"][idx]["scenarios"])

    def test_revenue_ranking_and_below_return_interval(self):
        payload = example("marsden-revenue")
        result = self.call("step", payload)
        self.assertEqual(result["ranking"]["status"], "ranked")
        rows = result["top_candidates"]
        amounts = [row["economics"]["gross_revenue_per_ha"] for row in rows]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        self.assertTrue(all(row["economics"]["price_reference_years"] == [2024] for row in rows))
        self.assertTrue(all("operating_expenses_per_ha" not in row["economics"] for row in rows))
        for row in rows:
            econ = row["economics"]
            self.assertAlmostEqual(
                econ["revenue_after_seed_per_ha"],
                econ["gross_revenue_per_ha"] - econ["seed_cost"]["cost_per_ha"],
                places=1,
            )
        after_seed_payload = payload | {
            "farmer": payload["farmer"] | {"priorities": ["revenue_after_seed"]}
        }
        after_seed = self.call("step", after_seed_payload)
        self.assertEqual(after_seed["ranking"]["rank_metric"], "revenue_after_seed_per_ha")
        amounts = [
            row["economics"]["revenue_after_seed_per_ha"] for row in after_seed["top_candidates"]
        ]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        supplied = payload["candidates"][:-1] + [
            {
                "crop_id": "soybeans",
                "economics": {
                    "marketable_yield_t_ha": 2,
                    "farmgate_price_per_t": 100,
                    "currency": "EUR",
                    "source": "Test price assumption",
                },
            }
        ]
        result = self.call("step", payload | {"candidates": supplied})
        self.assertEqual(result["ranking"]["status"], "priority_unavailable")
        self.assertEqual(result["ranking"]["ranked_ids"], [])
        result = self.call("step", after_seed_payload | {"candidates": supplied})
        self.assertEqual(result["ranking"]["status"], "priority_unavailable")
        soybean = next(row for row in result["candidates"] if row["crop_id"] == "soybeans")
        self.assertEqual(soybean["economics"]["gross_revenue_per_ha"], 200)
        self.assertIsNone(soybean["economics"]["revenue_after_seed_per_ha"])
        repeated = self.call(
            "step",
            payload
            | {
                "planting_date": "2041-05-01",
                "selected": [{"crop_id": "sunflower", "planting_date": "2040-05-01"}],
            },
        )
        sunflower = next(row for row in repeated["candidates"] if row["crop_id"] == "sunflower")
        self.assertEqual(sunflower["same_crop_return_reference_year"], 2043)
        self.assertNotIn("sunflower", repeated["ranking"]["selection_ids"])
        self.assertTrue(
            all(row["fallow_days_before_planting"] > 0 for row in sunflower["scenarios"].values())
        )

    def test_coordinates_with_supplied_profile(self):
        payload = example("sutherland-step-1")
        payload.pop("field_id")
        result = self.call(
            "step",
            payload
            | {
                "field": {
                    "name": "Coordinate-based planning",
                    "latitude": 42.9269,
                    "longitude": -95.5391,
                },
                "soil_source": "User-supplied test of the coordinate route",
                "soil_layers": [
                    {"top_m": 0, "bottom_m": 1, "field_capacity": 0.3, "wilting_point": 0.15}
                ],
            },
        )
        self.assertIsNone(result["field"]["id"])
        self.assertEqual(result["available_water_capacity_mm"], 150)

    def test_named_hybrid_calendar_options_are_distinct(self):
        payload = example("marsden-revenue")
        candidates = [
            {
                "crop_id": "maize_grain",
                "choice_id": "farm-corn-a",
                "hybrid": {
                    "name": "Example farm hybrid",
                    "source_url": "https://example.com/farm-calendar",
                    "stage_lengths": [25, 35, 45, 25],
                },
            },
            {"crop_id": "maize_grain", "choice_id": "generic-corn"},
        ]
        result = self.call("step", payload | {"candidates": candidates})
        a, b = result["candidates"]
        self.assertEqual({a["choice_id"], b["choice_id"]}, {"farm-corn-a", "generic-corn"})
        self.assertNotEqual(
            a["phenology"]["stage_lengths_days"], b["phenology"]["stage_lengths_days"]
        )
        self.assertEqual(
            a["phenology"]["stage_lengths_source"], "https://example.com/farm-calendar"
        )
