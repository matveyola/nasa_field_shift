# Historical surface soil moisture feature and model search

Estimate volumetric moisture near 5 cm as a residual correction to the matching ERA5-Land daily value. ERA5-Land is the incumbent. The historical analysis uses a completed UTC day and its preceding dates. It inherits the archive publication delay and does not apply to future forecasts. Freeze and snow screening stays aligned with the existing historical model.

## Feature groups

| Group | Predictors tested | Source |
| --- | --- | --- |
| Surface and vertical history | ERA5-Land surface, intermediate and deep moisture, trailing 3- and 7-day means, 1- and 3-day wetting or drying, gradients between layers, shallow soil and air temperature, seasonal phase | [ERA5-Land](https://doi.org/10.5194/essd-13-4349-2021) |
| Weather and water demand | Rain, ET₀, humidity, VPD, wind, radiation, temperature range, 3/7/14/30-day rain and ET₀ balances, recent wet days and temperature accumulation | [ERA5 via Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api) |
| Soil hydraulics | Sand, silt, clay, organic matter, bulk density, depth of surface horizon, dominant component share, drainage, field capacity, wilting point, available water capacity, relative wetness and interactions with texture and atmospheric drying | [USDA SSURGO](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo) |
| Terrain | Copernicus center elevation, local relief, gradient and relative elevation from five points per site at about 250 m spacing | [Copernicus GLO-90 via Open-Meteo](https://open-meteo.com/en/docs/elevation-api) |
| Geography | Latitude and longitude, scored as an optional group to quantify geographic dependence | Observation station metadata |

The data runner validates archive coordinates, timestamps, units and physical bounds. A trailing window ends on the target date. Cache keys are tied to the public request and cache entries are hashed in the report. Elevation queries use at most 100 locations per call. The public weather archive reached its daily quota after supplying 39 of 47 requested stations. The cache-only run treats missing weather as missing input across every candidate, keeps the same target rows across comparisons, and records all affected sites. Missing-value medians, indicators and weighted standardization are derived within the fitting fold.

Satellite vegetation indices, actual irrigation, crop phenology, tile drainage records and field-specific management could explain differences between sensors and grid values. They have no validated, publication-time-matched history across these stations. MODIS was probed and the public ORNL interface allows at most ten composite dates per arbitrary coordinate request. A full five-year join needs hundreds of requests and a quality and availability policy for each composite. Such features are outside this run. [ORNL MODIS service](https://modis.ornl.gov/data/modis_webservice.html)

## Evaluation design

Fit with the v2 development sites through 2024. Use four outer geographical folds, and choose features and hyperparameters inside each outer fold with three inner geographical folds. Assign all records from a station's 2° block to one fold and remove fitting stations within 40 km of a held station. Weight stations equally. The search uses a single numerical thread per process, one derived feature matrix per group and fold, and reuses each fitted estimator across the two correction strengths.

Test the direct archive, half-strength station-weighted bias, ridge across every combination of the four optional groups, robust ridge, elastic net, approximate RBF kernel ridge, histogram gradient boosting and extremely randomized trees. For the nonlinear alternatives use six predefined bundles. Search regularization, residual strength, kernel scale, tree depth, minimum leaf size and boosting iterations. Rank by the mean of normalized MAE and RMSE against direct ERA5-Land. Within 0.5 percentage points prefer the simpler estimator and feature bundle. Use separate calibration stations to set a 90% target interval. The 2025 regional and Iowa sites have published earlier evaluations and serve as diagnostics after selection.

## Results

The search evaluated 248 configurations from 60 candidate columns on 26,453 fitting pairs from 27 stations. The final model was full-strength ridge with alpha 3,000 and all feature groups. The four inner selections chose ExtraTrees, boosting, robust ridge and ridge. Some elastic-net fits reached their 2,000-iteration cap. No elastic-net configuration was selected.

| Evaluation in m³/m³ | Pairs | ERA5-Land MAE / RMSE | Expanded search MAE / RMSE |
| --- | ---: | ---: | ---: |
| Nested held-out development stations | 26,453 | 0.11877 / 0.14729 | 0.07961 / 0.10185 |
| Previously evaluated 2025 Iowa farms | 1,680 | 0.05672 / 0.07365 | 0.05219 / 0.06530 |
| Previously evaluated 2025 regional stations | 1,209 | 0.11152 / 0.12344 | 0.10678 / 0.14321 |

In a fixed-recipe ablation on the tuning folds, removing soil raised RMSE from 0.07878 to 0.11203. Removing geography, terrain and weather individually raised it to 0.08923, 0.08230 and 0.08149, respectively. On a separate matched comparison restricted to the 25 development stations with full weather input, adding weather reduced RMSE from 0.09916 to 0.09350 with the other parameters fixed. Both ablations reuse development labels. The regional diagnostic still showed larger errors and covered 72.7% of observations within the nominal 90% interval. ERA5-Land remains the serving method. [Search results and inputs](src/field_shift_ml/evaluations/historical-surface-search.json)

The follow-up evaluated 16 combinations of correction strength and distance to the nearest fitting station. The correction fades linearly to zero at the candidate radius. Each outer fold retains its inner-selected estimator and selects the distance rule only inside that fold's fitting cohort. The inner rule must lower both MAE and RMSE by 5% and keep every site's RMSE within 10% of the reference, with ERA5-Land as the fallback. The nested follow-up scored 0.08371 MAE and 0.10230 RMSE. The final fixed recipe selected a 180 km radius. On the known regional cohort it reduced RMSE from 0.14321 to 0.11646, against 0.12344 for the reference. On the known Iowa cohort it scored 0.07385, against 0.06530 for the untapered model and 0.07365 for the reference. The test documents the trade-off across known cohorts. [Frozen follow-up](src/field_shift_ml/evaluations/historical-surface-frozen-diagnostics.json)

The 2026 probe audit excluded one station overlapping prior geography and one with zero quality-controlled measurements. Three additional probes had eligible sensor observations and incomplete archives for the historical predictors. They remain unscored. The eligible managed-farm count is zero.

## Reproduce

Use the pinned offline requirements from the API directory. Use the same observation and soil snapshots as the historical v2 benchmark, including the separate Iowa snapshot. Use `--fetch-missing` for a cold archive cache. For the recorded partial-weather snapshot use `--allow-missing-weather` and retain the response cache and the recorded per-site hashes.

```sh
uv run --locked python run.py search_historical_surface \
  --observations data/observations-full.csv \
  --soil data/soil-profiles-development.json \
  --iowa data/observations-ifc-v2.csv \
  --iowa-soil data/soil-profiles-v2.json \
  --cache data/archive-cache \
  --external-cache data/historical-search \
  --allow-missing-weather \
  --output data/historical-surface-search.json
```

Re-run the command with the options below to reproduce the cached follow-up. Prepare the observations and soil snapshot using the acquisition commands in the [surface forecast protocol](forecast-protocol.md).

```sh
--frozen-report src/field_shift_ml/evaluations/historical-surface-search.json \
--fresh-observations data/surface-50r1-observations.csv \
--fresh-manifest src/field_shift_ml/evaluations/surface-forecast-50r1-sites.json \
--fresh-soil data/surface-50r1-soils.json \
--output data/historical-surface-frozen-diagnostics.json
```
