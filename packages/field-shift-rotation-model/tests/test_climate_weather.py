"""Original handoff tests, adapted only for installed namespace/resources."""

from datetime import date
from unittest import TestCase
from unittest.mock import patch

from field_shift_rotation_model.modules.rotations import climate_weather as climate


class ClimateWeatherTests(TestCase):
    def test_bundled_cell_reuses_archive_and_preserves_leap_day(self):
        start, end = date(2040, 2, 28), date(2040, 3, 1)
        with patch.object(climate, "_download", side_effect=AssertionError("unexpected download")):
            result = climate.load_climate_weather(42.04, -93.80, start, end)
            self.assertEqual(result["delivery"], "bundled_snapshot")
            self.assertEqual([s["scenario"] for s in result["series"]], ["ssp245", "ssp585"])
            series = result["series"][0]
            self.assertEqual((series["grid_latitude"], series["grid_longitude"]), (42.125, -93.875))
            self.assertEqual(
                [r["date"] for r in series["daily"]], ["2040-02-28", "2040-02-29", "2040-03-01"]
            )
            self.assertEqual(len(series["sources"]), 6)

            series["daily"][0]["rain_mm"] = -900
            second = climate.load_climate_weather(42.04, -93.80, start, end)
            self.assertGreaterEqual(second["series"][0]["daily"][0]["rain_mm"], 0)

    def test_sutherland_snapshot_contains_three_complete_years_and_spinup(self):
        start, end = date(2039, 10, 1), date(2042, 12, 31)
        with patch.object(climate, "_download", side_effect=AssertionError("unexpected download")):
            result = climate.load_climate_weather(42.9269, -95.5391, start, end)
        self.assertEqual(result["delivery"], "bundled_snapshot")
        self.assertEqual([s["scenario"] for s in result["series"]], ["ssp245", "ssp585"])
        for series in result["series"]:
            self.assertEqual((series["grid_latitude"], series["grid_longitude"]), (42.875, -95.625))
            self.assertEqual(len(series["daily"]), 1188)
            self.assertEqual(len(series["sources"]), 24)

    def test_rejects_ineligible_dates_coordinates_and_sources(self):
        start, end = date(2040, 1, 1), date(2040, 1, 2)
        with patch.object(climate, "_download", side_effect=AssertionError("unexpected download")):
            for latitude, longitude in ((-60.01, 0), (90.01, 0), (45, 180.01), (float("nan"), 0)):
                with (
                    self.subTest(latitude=latitude, longitude=longitude),
                    self.assertRaises(ValueError),
                ):
                    climate.load_climate_weather(latitude, longitude, start, end)
            for first, last in ((end, start), (date(2014, 1, 1), end), (start, date(2101, 1, 1))):
                with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                    climate.load_climate_weather(42, -93, first, last)
            for selectors in (
                {"models": ["../unauthorized"]},
                {"scenarios": ["historical"]},
                {"models": []},
            ):
                with self.subTest(selectors=selectors), self.assertRaises(ValueError):
                    climate.load_climate_weather(42, -93, start, end, **selectors)

    def test_online_series_converts_units_and_records_an_invalid_humidity_value(self):
        readings = {
            "tasmin": 270,
            "tasmax": 280,
            "pr": 0.0001,
            "hurs": 101,
            "rsds": 100,
            "sfcWind": 3,
        }

        def fetch(lat, lon, model, scenario, year, variable):
            self.assertEqual((lat, lon), (48.875, 2.375))
            return climate._VariableYear(
                climate._subset_url(lat, lon, model, scenario, year, variable),
                "proleptic_gregorian",
                climate._UNITS[variable],
                "days since 1850-01-01",
                "CC-BY 4.0",
                "CSIRO-ARCCSS",
                "source",
                (readings[variable],),
            )

        day = date(2040, 1, 1)
        with patch.object(climate, "_fetch_variable", side_effect=fetch):
            result = climate.load_climate_weather(48.92, 2.3, day, day, scenarios=["ssp245"])
        self.assertEqual(result["delivery"], "nasa_nccs_ncss")
        series = result["series"][0]
        self.assertEqual(
            series["daily"][0],
            {
                "date": "2040-01-01",
                "tmin_c": -3.15,
                "tmax_c": 6.85,
                "rain_mm": 8.64,
                "rh_mean_pct": 100,
                "rs_mj_m2": 8.64,
                "wind_10m_m_s": 3,
            },
        )
        self.assertEqual(series["quality"]["rh_clipped_days"], 1)

    def test_provider_error_does_not_return_other_weather(self):
        day = date(2040, 1, 1)
        with patch.object(climate, "_download", side_effect=climate.ClimateWeatherError("offline")):
            with self.assertRaisesRegex(climate.ClimateWeatherError, "offline"):
                climate.load_climate_weather(48.92, 2.3, day, day, scenarios=["ssp245"])
