"""Run an installed offline workflow without depending on the checkout location."""

import argparse
import runpy
import sys

WORKFLOWS = (
    "evaluate_moisture",
    "fetch_observations",
    "fetch_root_zone_observations",
    "fetch_soil_profiles",
    "fetch_surface_forecast_observations",
    "iterate_rotation_yield",
    "research_root_zone",
    "research_surface_forecast",
    "root_zone_history",
    "root_zone_soil",
    "search_historical_surface",
    "search_root_zone",
    "search_rotation_yield",
    "train_moisture",
    "train_moisture_forecast",
    "train_moisture_v2",
    "train_root_zone",
    "train_rotation_yield",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", choices=WORKFLOWS)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()

    sys.argv[1:] = options.args
    runpy.run_module(f"field_shift_ml.{options.workflow}", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
