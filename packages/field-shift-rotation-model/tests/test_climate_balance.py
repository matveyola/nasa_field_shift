"""Original handoff tests, adapted only for installed namespace/resources."""

import gzip
import json
import unittest
from datetime import date
from importlib.resources import files

from field_shift_rotation_model.modules.rotations.climate_balance import (
    ClimateBalanceError,
    compare_candidates,
    engine_directory,
    required_weather_dates,
)

EXAMPLES = files("field_shift_rotation_model").joinpath("examples")


class ClimateBalanceDatesTests(unittest.TestCase):
    def test_spin_up_includes_intervening_fallow_years(self):
        history = [{"planting_date": "2040-05-01"}, {"planting_date": date(2042, 4, 1)}]
        self.assertEqual(
            required_weather_dates("2044-05-01", history),
            (date(2039, 10, 1), date(2044, 12, 31)),
        )

    def test_rejects_same_year_and_out_of_season_sowing(self):
        with self.assertRaisesRegex(ClimateBalanceError, "one sowing per year"):
            required_weather_dates("2040-05-01", [{"planting_date": "2040-04-01"}])
        with self.assertRaisesRegex(ClimateBalanceError, "March through July"):
            required_weather_dates("2040-10-01")


class ClimateBalanceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        example = json.loads((EXAMPLES / "marsden-ssurgo-water-stress.json").read_text())
        layers = [
            dict(hydraulic, saturation=saturated["saturation"])
            for hydraulic, saturated in zip(
                example["field"]["soil_layers"],
                example["waterlogging_soil"]["layers"],
                strict=True,
            )
        ]
        climate_dir = EXAMPLES / "climate"
        with gzip.open(climate_dir / "climate-weather.json.gz", "rt") as stream:
            climate = json.load(stream)
        cls.inputs = {
            "latitude": example["field"]["latitude"],
            "longitude": example["field"]["longitude"],
            "soil_source": example["waterlogging_soil"]["source"],
            "soil_layers": layers,
            "historical_weather": json.loads((climate_dir / "calendar-weather.json").read_text()),
            "climate_weather": climate,
            "ruslan_dir": engine_directory(),
        }

    def test_alternatives_share_prior_crop_and_opening_state(self):
        previous = [{"crop_id": "maize_grain", "planting_date": "2040-05-01"}]
        report = compare_candidates(
            **self.inputs,
            candidates=["maize_grain", "sunflower", "wheat_winter_frozen"],
            planting_date="2042-05-01",
            selected=previous,
        )
        first, second, unavailable = report["candidates"]
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(unavailable["status"], "unsupported")
        self.assertEqual(len(report["weather_members"]), 4)
        self.assertAlmostEqual(report["available_water_capacity_mm"], 140.2)
        self.assertEqual(report["selected_scenarios"], first["selected_scenarios"])
        self.assertEqual(first["selected_scenarios"], second["selected_scenarios"])
        for key, row in first["scenarios"].items():
            self.assertEqual(
                row["profile_opening_mm"], second["scenarios"][key]["profile_opening_mm"]
            )
            self.assertEqual(
                row["opening_snow_water_mm"], second["scenarios"][key]["opening_snow_water_mm"]
            )
            self.assertEqual(
                row["previous_end_date"], first["selected_scenarios"][key][0]["end_date"]
            )
            self.assertGreater(row["fallow_days_before_planting"], 365)
            self.assertIsNone(row["metrics"]["water_stress_days"])
            self.assertIsNone(row["metrics"]["waterlogging_stress_days"])

    def test_selected_history_is_available_if_candidate_is_unsupported(self):
        previous = [{"crop_id": "maize_grain", "planting_date": "2040-05-01"}]
        report = compare_candidates(
            **self.inputs,
            candidates=["wheat_winter_frozen"],
            planting_date="2041-05-01",
            selected=previous,
        )
        self.assertEqual(report["candidates"][0]["status"], "unsupported")
        self.assertEqual(set(report["selected_scenarios"]), set(report["weather_members"]))
        self.assertTrue(all(len(rows) == 1 for rows in report["selected_scenarios"].values()))

    def test_missing_weather_fails_instead_of_comparing_incomplete_members(self):
        original = self.inputs["climate_weather"]
        series = original["series"][0]
        missing = dict(
            series, daily=[row for row in series["daily"] if row["date"] != "2040-02-29"]
        )
        inputs = self.inputs | {"climate_weather": dict(original, series=[missing])}
        with self.assertRaisesRegex(ClimateBalanceError, "2040-02-29"):
            compare_candidates(**inputs, candidates=["maize_grain"], planting_date="2040-05-01")
