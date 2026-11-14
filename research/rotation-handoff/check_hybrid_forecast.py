"""Offline regression tests for the live-to-historical weather transition."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from pydantic import ValidationError

from field_shift.modules.rotations import hybrid_forecast as hybrid
from field_shift.modules.rotations.erosion_inputs import Erodibility
from field_shift.modules.rotations.global_soil import SoilProfile
from field_shift.modules.rotations.historical_fields import WeatherDay, load_fields
from field_shift.modules.rotations.hybrid_profiles import crop_catalog
from field_shift.modules.rotations.hybrid_weather import (
    WeatherProviderError,
    WeatherSeries,
)
from field_shift.modules.rotations.waterlogging import WaterloggingEstimate
from field_shift.modules.rotations.waterlogging_inputs import (
    SoilWaterloggingProfile,
    WaterloggingLayer,
)

TODAY = date(2026, 11, 12)
DAY = timedelta(days=1)
CROPS = ("maize_grain", "sunflower")


def weather_rows(start, end, source, *, opening_rain=0):
    days = {}
    for index in range((end - start).days + 1):
        day = start + index * DAY
        days[day] = WeatherDay(
            date=day,
            rain_mm=opening_rain if not index else 0,
            low_c=14,
            high_c=26,
            reference_et0_mm=5,
        )
    return WeatherSeries(
        days,
        f"https://example.invalid/{source}",
        source,
        42.0,
        -93.8,
        dict.fromkeys(days, (0.31, 0.30, 0.29)),
    )


def live_weather(latitude, longitude, *, as_of, days):
    return weather_rows(
        as_of, as_of + (days - 1) * DAY, "live_forecast", opening_rain=40
    )


def archived_weather(latitude, longitude, start, end):
    return weather_rows(start, end, "historical_analog")


def make_request(start=TODAY, duration=12, initial=True):
    planting = {
        "planting_date": start,
        "through_date": start + (duration - 1) * DAY,
    }
    if initial:
        planting["initial_moisture"] = {"theta": [0, 0, 0], "source": "test input"}
    return hybrid.ForecastRequest.model_validate(
        {
            "field_id": "marsden_demo",
            "seasons": [planting],
            "plans": [{"id": crop, "crops": [{"crop_id": crop}]} for crop in CROPS],
        }
    )


class TestHybridForecast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field = load_fields()["marsden_demo"]

    def run_forecast(self, request):
        with (
            patch.object(hybrid, "fetch_live", side_effect=live_weather),
            patch.object(hybrid, "fetch_history", side_effect=archived_weather),
        ):
            return hybrid.forecast(request, self.field, as_of=TODAY)

    def test_balance_continues_across_source_boundary(self):
        result = self.run_forecast(make_request())
        self.assertEqual(result.weather.member_count, 5)
        live_only = self.run_forecast(make_request(duration=7))
        self.assertEqual(live_only.weather.member_count, 1)
        self.assertEqual(live_only.weather.interpretation, "live_only")
        self.assertIsNone(live_only.weather.historical_source_url)
        for plan, prefix in zip(result.plans, live_only.plans, strict=True):
            for member in plan.members:
                season = member.seasons[0]
                before, after = season.weather_segments
                self.assertEqual(before.source, "live_forecast")
                self.assertEqual(before.dates.end, TODAY + 6 * DAY)
                self.assertEqual(after.source, "historical_analog")
                self.assertEqual(after.dates.start, TODAY + 7 * DAY)
                self.assertEqual(
                    after.historical_dates.start,
                    member.historical_anchor_date + 7 * DAY,
                )
                # The only rain was in the live part and the field began dry.
                # Continued water use after day seven requires retaining that water.
                self.assertGreater(
                    season.modeled_use_mm, prefix.members[0].seasons[0].modeled_use_mm
                )

    def test_later_planting_uses_archive_and_analogue_moisture(self):
        request = make_request(start=TODAY + 21 * DAY, initial=False)
        with (
            patch.object(
                hybrid, "fetch_live", side_effect=AssertionError("Unexpected live call")
            ),
            patch.object(hybrid, "fetch_history", side_effect=archived_weather),
        ):
            result = hybrid.forecast(request, self.field, as_of=TODAY)
        self.assertIsNone(result.weather.live_source_url)
        for member in result.plans[0].members:
            season = member.seasons[0]
            self.assertEqual(len(season.weather_segments), 1)
            self.assertEqual(season.weather_segments[0].source, "historical_analog")
            self.assertEqual(
                season.opening_historical_date, member.historical_anchor_date - DAY
            )

    def test_provider_failure_does_not_substitute_history_for_live_days(self):
        with (
            patch.object(
                hybrid, "fetch_live", side_effect=WeatherProviderError("test outage")
            ),
            patch.object(
                hybrid,
                "fetch_history",
                side_effect=AssertionError("Unexpected archive call"),
            ),
            self.assertRaisesRegex(WeatherProviderError, "test outage"),
        ):
            hybrid.forecast(make_request(), self.field, as_of=TODAY)

    def test_catalog_profiles_can_all_be_used(self):
        profiles = crop_catalog()
        self.assertEqual(len(profiles), 13)
        for profile in profiles:
            request = make_request()
            crop = request.plans[0].crops[0]
            crop.crop_id = profile.id
            crop.stage_lengths = (25, 35, 45, 25)
            result = self.run_forecast(request)
            self.assertEqual(result.plans[0].members[0].seasons[0].crop_id, profile.id)

    def test_management_inputs_and_farmer_priorities(self):
        payload = make_request().model_dump()
        payload["farmer"] = {
            "equipment_access": {"combine": "contracted", "sprinkler": "owned"},
            "irrigation_available": True,
            "irrigation_system": "sprinkler",
            "priorities": ["water_saving"],
        }
        dry, wet = payload["plans"]
        dry["required_equipment"] = ["combine"]
        wet["required_equipment"] = ["sprinkler"]
        wet["irrigation"] = [{"date": TODAY + 2 * DAY, "net_mm": 150}]
        wet["soil"] = {
            "living_cover": [
                {"start": TODAY, "end": TODAY + 3 * DAY},
                {"start": TODAY + 2 * DAY, "end": TODAY + 4 * DAY},
            ],
            "erosion": {
                "r": 100,
                "k": 0.04,
                "ls": 1,
                "c": 0.6,
                "p": 1,
                "source": "test",
            },
            "harvests": [
                {
                    "yield_t_ha": 2,
                    "p2o5_kg_per_t": 7.5,
                    "k2o_kg_per_t": 6.5,
                    "source": "test",
                }
            ],
        }
        result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        water = result.plans[1].members[0].seasons[0].water_management
        self.assertEqual(water.applied_irrigation_mm, 150)
        self.assertGreater(water.modeled_drainage_mm, 0)
        self.assertIsNone(water.waterlogging_stress_days)
        self.assertEqual(result.plans[1].soil.living_cover_days, 5)
        self.assertEqual(result.plans[1].soil.erosion_t_ha_year, 2.4)
        self.assertEqual(result.plans[1].soil.p2o5_removed_kg_ha, 15)
        self.assertIsNone(result.plans[0].soil.living_cover_days)
        self.assertEqual(result.decision.priorities_used, ["water_saving"])
        self.assertEqual(result.decision.priorities_missing, [])
        self.assertEqual([plan.rank for plan in result.decision.decisions], [1, 2])
        payload["farmer"]["irrigation_available"] = False
        denied = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(
            denied.decision.decisions[1].blockers, ["irrigation_unavailable"]
        )
        self.assertIsNone(denied.decision.decisions[1].rank)

    def test_margin_and_revenue_use_supplied_yield_price_and_cost(self):
        payload = make_request().model_dump()
        payload["seasons"][0]["through_date"] = None
        payload["farmer"] = {"priorities": ["revenue"]}
        for plan, values in zip(
            payload["plans"], [(8, 200, 1400), (3, 500, 900)], strict=True
        ):
            plan["required_equipment"] = []
            harvest, price, cost = values
            plan["crops"][0]["economics"] = {
                "marketable_yield_t_ha": harvest,
                "farmgate_price_per_t": price,
                "included_cost_per_ha": cost,
                "currency": "USD",
                "source": "test",
            }
        result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(
            [plan.economics.gross_margin_per_ha for plan in result.plans], [200, 600]
        )
        self.assertEqual([plan.rank for plan in result.decision.decisions], [1, 2])
        payload["plans"][1]["crops"][0]["economics"]["currency"] = "EUR"
        mixed = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(mixed.decision.priorities_missing, ["revenue"])
        self.assertEqual([plan.rank for plan in mixed.decision.decisions], [None, None])

    def test_one_selected_priority_controls_the_ranking(self):
        payload = make_request().model_dump()
        payload["seasons"][0]["through_date"] = None
        payload["farmer"] = {"priorities": ["revenue", "yield"]}
        with self.assertRaisesRegex(ValidationError, "at most 1 item"):
            hybrid.ForecastRequest.model_validate(payload)

        payload["farmer"]["priorities"] = ["revenue"]
        for plan, (harvest, price) in zip(
            payload["plans"], [(8, 150), (3, 500)], strict=True
        ):
            plan["required_equipment"] = []
            plan["crops"][0]["economics"] = {
                "marketable_yield_t_ha": harvest,
                "farmgate_price_per_t": price,
                "currency": "USD",
                "source": "test",
            }
        result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(result.decision.priorities_used, ["revenue"])
        self.assertEqual([plan.rank for plan in result.decision.decisions], [2, 1])
        self.assertTrue(
            all(plan.economics.gross_margin_per_ha is None for plan in result.plans)
        )

        payload["farmer"]["priorities"] = ["yield"]
        by_yield = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(by_yield.decision.priorities_used, ["yield"])
        self.assertEqual([plan.rank for plan in by_yield.decision.decisions], [1, 2])
        self.assertEqual(
            [plan.values["yield"] for plan in by_yield.decision.decisions], [8, 3]
        )

        payload["farmer"]["priorities"] = ["revenue"]
        payload["plans"][0]["crops"][0]["economics"]["farmgate_price_per_t"] = 187.5
        tied = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual([plan.rank for plan in tied.decision.decisions], [1, 1])

        payload["farmer"]["priorities"] = ["soil_cover"]
        missing = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(missing.decision.priorities_missing, ["soil_cover"])
        self.assertEqual([plan.rank for plan in missing.decision.decisions], [None, None])

    def test_information_only_metrics_are_not_ranking_priorities(self):
        payload = make_request().model_dump()
        for priority in ("erosion", "excess_water", "profit"):
            with self.subTest(priority=priority):
                payload["farmer"] = {"priorities": [priority]}
                with self.assertRaises(ValidationError) as caught:
                    hybrid.ForecastRequest.model_validate(payload)
                self.assertEqual(
                    caught.exception.errors()[0]["loc"], ("farmer", "priorities", 0)
                )

    def test_deficit_can_rank_but_combined_stress_requires_every_plan(self):
        payload = make_request().model_dump()
        payload["farmer"] = {"priorities": ["water_stress"]}
        for plan in payload["plans"]:
            plan["required_equipment"] = []
        soil = SoilWaterloggingProfile((WaterloggingLayer(0, 1, 0.38, 1),))
        with patch.object(
            hybrid, "fetch_waterlogging_inputs", return_value=soil
        ) as fetch:
            combined = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        fetch.assert_called_once()
        self.assertFalse(combined.water_stress_comparable)
        self.assertEqual(combined.decision.priorities_missing, ["water_stress"])
        self.assertTrue(all(plan.rank is None for plan in combined.decision.decisions))

        payload["farmer"]["priorities"] = ["water_deficit"]
        deficit = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(deficit.decision.priorities_used, ["water_deficit"])
        self.assertTrue(all(plan.rank is not None for plan in deficit.decision.decisions))
        for plan, decision in zip(deficit.plans, deficit.decision.decisions, strict=True):
            self.assertEqual(decision.values["water_deficit"], plan.median_pct)

    def test_combined_stress_counts_overlapping_dates_once(self):
        payload = make_request().model_dump()
        payload["farmer"] = {"priorities": ["water_stress"]}
        for plan in payload["plans"]:
            plan["required_equipment"] = []
        estimate = WaterloggingEstimate.model_construct(estimated_crop_stress_days=1)
        soil = SoilWaterloggingProfile((WaterloggingLayer(0, 1, 0.55, 20),))
        with (
            patch.object(hybrid, "fetch_waterlogging_inputs", return_value=soil),
            patch.object(
                hybrid, "estimate_waterlogging",
                return_value=(estimate, frozenset({TODAY})),
            ),
        ):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertTrue(result.water_stress_comparable)
        self.assertEqual(result.decision.priorities_used, ["water_stress"])
        member = result.plans[0].members[0]
        self.assertEqual(member.waterlogging_stress_days, 1)
        self.assertEqual(member.water_stress_days, member.drought_stress_days)
        self.assertEqual(member.water_stress_days_pct, 100)

    def test_combined_priority_is_unavailable_when_one_plan_is_missing_wet_stress(self):
        payload = make_request().model_dump()
        payload["farmer"] = {"priorities": ["water_stress"]}
        for plan in payload["plans"]:
            plan["required_equipment"] = []
        payload["plans"][1]["crops"][0].update(
            crop_id="millet", calendar_id=None, stage_lengths=[20, 25, 35, 20]
        )
        soil = SoilWaterloggingProfile((WaterloggingLayer(0, 1, 0.55, 20),))
        with patch.object(hybrid, "fetch_waterlogging_inputs", return_value=soil):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertIsNotNone(result.plans[0].median_water_stress_days_pct)
        self.assertIsNone(result.plans[1].median_water_stress_days_pct)
        self.assertFalse(result.water_stress_comparable)
        self.assertEqual(result.decision.priorities_missing, ["water_stress"])
        self.assertTrue(all(plan.rank is None for plan in result.decision.decisions))

    def test_waterlogging_uses_persistent_wetness_and_keeps_water_shortfall(self):
        payload = make_request().model_dump()
        payload["seasons"][0]["initial_moisture"]["theta"] = [0.5, 0.5, 0.5]
        payload["estimate_waterlogging"] = True
        soil = SoilWaterloggingProfile((WaterloggingLayer(0, 1, 0.5, 0.1),))
        with patch.object(hybrid, "fetch_waterlogging_inputs", return_value=soil):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(result.waterlogging_soil_status, "estimated_from_soilgrids")
        water = result.plans[0].members[0].seasons[0].water_management
        wet = water.waterlogging
        self.assertIsNotNone(wet)
        self.assertGreater(wet.days_above_crop_aeration_threshold, 1)
        self.assertEqual(
            wet.estimated_crop_stress_days, wet.days_above_crop_aeration_threshold - 1
        )
        self.assertLess(wet.minimum_crop_aeration_factor, 1)
        self.assertEqual(water.waterlogging_stress_days, wet.estimated_crop_stress_days)
        payload["estimate_waterlogging"] = False
        reference = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(result.plans[0].median_pct, reference.plans[0].median_pct)

        payload["estimate_waterlogging"] = True
        with patch.object(
            hybrid, "fetch_waterlogging_inputs",
            side_effect=hybrid.SoilProviderError("Test unavailable data"),
        ):
            unavailable = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(unavailable.waterlogging_soil_status, "soil_data_unavailable")
        self.assertIsNone(unavailable.plans[0].members[0].seasons[0].water_management.waterlogging)

        incompatible_threshold = SoilWaterloggingProfile(
            (WaterloggingLayer(0, 1, 0.38, 1),)
        )
        with patch.object(
            hybrid, "fetch_waterlogging_inputs", return_value=incompatible_threshold
        ):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        wet = result.plans[0].members[0].seasons[0].water_management.waterlogging
        self.assertEqual(
            wet.crop_stress_status, "aeration_threshold_not_above_field_capacity"
        )
        self.assertIsNone(wet.estimated_crop_stress_days)

    def test_erosion_uses_estimated_k_only_with_other_required_inputs(self):
        payload = make_request().model_dump()
        payload["country_code"] = "UKR"
        payload["estimate_erosion_erodibility"] = True
        payload["plans"][0]["soil"] = {
            "erosion_management": {
                "slope_percent": 2, "slope_length_m": 30,
                "c": 0.2, "p": 1, "source": "test management",
            }
        }
        with (
            patch.object(hybrid, "fetch_erodibility", return_value=Erodibility(0.04, 25, 45, 30, 2)),
            patch.object(hybrid, "rainfall_erosivity", return_value=100),
        ):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        complete, incomplete = result.plans
        self.assertEqual(complete.erosion_inputs_missing, [])
        self.assertAlmostEqual(complete.soil.erosion_t_ha_year, 0.212)
        self.assertEqual(incomplete.soil_erodibility_k, 0.04)
        self.assertEqual(incomplete.erosion_inputs_missing, ["ls", "c", "p"])
        self.assertIsNone(incomplete.soil.erosion_t_ha_year)

    def test_supplied_hydraulic_profile_does_not_require_a_provider(self):
        payload = make_request().model_dump()
        payload["waterlogging_soil"] = {
            "source": "test hydraulic profile",
            "layers": [
                {
                    "top_m": 0,
                    "bottom_m": 1,
                    "saturation": 0.55,
                    "saturated_conductivity_mm_day": 30,
                }
            ],
        }
        with patch.object(
            hybrid, "fetch_waterlogging_inputs", side_effect=AssertionError("external call")
        ):
            result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(result.waterlogging_soil_status, "supplied")
        wet = result.plans[0].members[0].seasons[0].water_management.waterlogging
        self.assertEqual(wet.soil_sources, ["test hydraulic profile"])

    def test_marsden_defaults_and_equipment_multiselect(self):
        payload = make_request().model_dump()
        payload["seasons"][0]["through_date"] = None
        payload["farmer"] = {
            "equipment_ids": ["row_planter"],
            "equipment_access": {"grain_combine": "contracted"},
            "irrigation_available": False,
            "priorities": ["revenue"],
        }
        payload["plans"][1]["soil"] = {
            "erosion_management": {"c": 0.2, "p": 1, "source": "test only"}
        }
        result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertIsNotNone(result.field_references_url)
        maize, sunflower = result.plans
        self.assertEqual(maize.economics.input_origins, ["published_benchmark"])
        self.assertIsNotNone(maize.economics.marketable_yield_t_ha)
        self.assertIsNotNone(sunflower.economics.gross_margin_per_ha)
        self.assertIsNotNone(maize.soil.p2o5_removed_kg_ha)
        self.assertIsNotNone(sunflower.soil.k2o_removed_kg_ha)
        self.assertEqual(maize.erosion_inputs_missing, ["c", "p"])
        self.assertEqual(sunflower.erosion_inputs_missing, [])
        self.assertAlmostEqual(sunflower.soil.erosion_t_ha_year, 5.69, places=2)
        self.assertEqual(maize.required_equipment, ["row_planter", "grain_combine"])
        self.assertEqual(maize.equipment_requirements_source, "catalog_baseline")
        self.assertTrue(
            all(item.feasibility == "feasible" for item in result.decision.decisions)
        )
        payload["farmer"]["equipment_access"] = {}
        result = self.run_forecast(hybrid.ForecastRequest.model_validate(payload))
        self.assertEqual(
            result.decision.decisions[0].blockers,
            ["equipment_unavailable:grain_combine"],
        )

    def test_other_fields_and_explicit_empty_harvests(self):
        payload = make_request().model_dump()
        payload["seasons"][0]["through_date"] = None
        payload["plans"][0]["soil"] = {"harvests": []}
        request = hybrid.ForecastRequest.model_validate(payload)
        marsden = self.run_forecast(request)
        self.assertEqual(marsden.plans[0].soil.p2o5_removed_kg_ha, 0)
        self.assertIsNotNone(marsden.plans[1].soil.p2o5_removed_kg_ha)
        with (
            patch.object(hybrid, "fetch_live", side_effect=live_weather),
            patch.object(hybrid, "fetch_history", side_effect=archived_weather),
        ):
            other = hybrid.forecast(
                request, load_fields()["sutherland_nw"], as_of=TODAY
            )
        self.assertIsNone(other.field_references_url)
        self.assertEqual(other.country_code, "USA")
        self.assertEqual(
            other.country_references_url, "/api/rotations/forecast/countries/USA"
        )
        self.assertIsNotNone(other.plans[1].economics.marketable_yield_t_ha)
        self.assertIsNone(other.plans[1].economics.gross_margin_per_ha)
        self.assertIsNotNone(other.plans[1].soil.p2o5_removed_kg_ha)

    def test_global_coordinates_load_soil_and_country_benchmarks(self):
        payload = make_request().model_dump()
        payload["field_id"] = None
        payload["field"] = {
            "id": "vinnytsia",
            "name": "Vinnytsia demo",
            "latitude": 49.2,
            "longitude": 28.4,
        }
        payload["seasons"][0]["through_date"] = None
        request = hybrid.ForecastRequest.model_validate(payload)
        with (
            patch.object(
                hybrid,
                "fetch_soil_profile",
                return_value=SoilProfile(self.field.soil_layers),
            ) as soil,
            patch.object(hybrid, "fetch_live", side_effect=live_weather),
            patch.object(hybrid, "fetch_history", side_effect=archived_weather),
        ):
            result = hybrid.forecast(request, request.field, as_of=TODAY)
        soil.assert_called_once_with(49.2, 28.4)
        self.assertEqual(result.country_code, "UKR")
        self.assertEqual(result.country_code_source, "natural_earth")
        self.assertIsNotNone(result.rainfall_erosivity_r)
        self.assertEqual(
            result.plans[0].economics.input_origins, ["published_benchmark"]
        )
        self.assertIsNotNone(result.plans[0].economics.inputs[0].farmgate_price_per_t)
        self.assertIsNotNone(result.plans[0].economics.gross_revenue_per_ha)
        self.assertIsNotNone(result.plans[0].soil.p2o5_removed_kg_ha)
        self.assertIsNone(result.plans[0].economics.gross_margin_per_ha)
        self.assertIsNone(result.plans[0].soil.erosion_t_ha_year)
        self.assertEqual(result.plans[0].erosion_inputs_missing, ["k", "ls", "c", "p"])


if __name__ == "__main__":
    unittest.main()
