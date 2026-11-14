# Field Shift offline ML

This package owns training, acquisition and evaluation for soil moisture, water
forecasts and rotation effects. `src/field_shift_ml/evaluations` contains frozen
protocols and reports. New downloads, caches and candidate artifacts belong in
the ignored `data` directory.

Use the package from this repository checkout. The `run.py` launcher makes the
API source available to offline workflows so they use the same feature and
forecast helpers as the runtime. The ML environment has its own lockfile and
preserves the original numerical dependencies. Only validated artifacts and
inference code ship with the API and workers.

## Soil moisture model

The target is daily volumetric water content in m³/m³ at a sensor depth of approximately 5 cm. The model estimates a correction to ERA5-Land 0–7 cm moisture. Its inputs are the current day's three ERA5-Land moisture layers, trailing three- and seven-day means of the surface layer, shallow soil and air temperatures, and two calendar encodings. These predictors share one implementation in training and inference. The target and predictors use UTC calendar days. [ERA5-Land](https://doi.org/10.24381/cds.e2161bac) · [Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)

The targets come from the NOAA USCRN 5 cm and USDA NRCS SCAN 2-inch probes. At least 20 qualified hours spanning all four six-hour intervals are required for a daily target. USDA volumetric percentages are converted to fractions. Both pipelines exclude modeled shallow soil temperatures below 2 °C and modeled snow depths of at least 1 cm. [NOAA USCRN](https://www.ncei.noaa.gov/access/crn/products.html) · [USDA SCAN](https://www.nrcs.usda.gov/resources/data-and-reports/soil-climate-analysis-network)

## Evaluation protocol

- Fit uses 2021–2024 data. Candidate selection compares regularized linear and boosted-tree corrections using geographically grouped cross-validation within fitting data.
- Calibration uses separate 2021–2024 locations. The interval uses the 90th percentile absolute error weighted equally across calibration stations.
- The main 2025 holdout uses locations in reserved 2° blocks. A 40 km buffer and the block boundaries separate training, calibration, and holdout locations. Every station receives equal weight in the metrics regardless of its number of days.
- Ames, Rogers Farm, Molly Caren, and Busby Farm are separately reserved agricultural SCAN sites. Their surrounding areas are excluded from all fitting and calibration. The first three contribute 2025 observations. Busby Farm lacks 2025 coverage and has a separately reported spatial-only score for 2021–2024.
- Promotion requires at least three unseen stations in each 2025 cohort, at least 3% improvement in both station-balanced MAE and RMSE over raw ERA5-Land on the main holdout, and at least 70% observed interval coverage there. On the independently held-out farms, MAE and RMSE cannot both worsen by more than 10%. The artifact records all checks, both cohorts' metrics, the station manifest and input hashes.

## Candidate results

`fieldshift-vwc-bb3c6d0539ec` is an experimental boosted-tree correction. The downloaded archive contains 71,659 daily observations from 53 stations. The fitting cohort contains 29,056 paired thawed days from 30 stations, and the calibration cohort contains 6,181 days from seven stations.

| Held-out cohort | Sites | Days | ERA5-Land MAE | Model MAE | ERA5-Land RMSE | Model RMSE | Observed 90% interval coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Main geographic and 2025 holdout | 5 | 1,209 | 0.1115 | 0.0527 | 0.1234 | 0.0624 | 96.0% |
| Independent SCAN farm sites, 2025 | 3 | 711 | 0.0536 | 0.0928 | 0.0663 | 0.1130 | 70.7% |
| Independent Busby Farm, 2021–2024 | 1 | 880 | 0.0619 | 0.1003 | 0.0729 | 0.1142 | 61.0% |

The correction fails the independent-farm release gate. ERA5-Land remains the default. The bundled artifact supports an explicitly labeled experimental comparison when enabled. Errors are reported in m³/m³. The variation across cohorts shows why a geographically pooled score alone is insufficient for deciding farm use.

The frozen candidate was also evaluated against three independently sourced probes in corn and soybean fields. It scored 0.1109 RMSE across 765 paired days, compared with 0.0739 for ERA5-Land. The corn probe improved, while both soybean probes worsened. The nearest fitting or calibration station was 96 km away. One corn probe shares a 2° block with a fitting station 120 km away. [Independent field report](src/field_shift_ml/evaluations/iowa-fields-v1.json) · [Iowa Flood Center dataset](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/)

## Reproduce

Run from `packages/field-shift-ml` in a dedicated Python 3.11 or 3.12 environment. Cached sources and candidate artifacts stay in the ignored `data` directory.

```sh
uv sync --locked --python 3.12
uv run --locked python run.py fetch_observations --output data/observations.csv --cache-dir data/observations-raw
uv run --locked python run.py train_moisture --observations data/observations.csv --cache data/archive --output data/moisture-v1.json
```

Compare the observation and joined sample hashes, metrics, and eligibility in the candidate JSON before replacing the bundled artifact under `apps/api/src/field_shift/modules/geo_ml/artifacts`. The production worker runs the portable, content-versioned JSON using the application runtime. The training dependencies stay outside the worker. For commercial archive requests, set `OPEN_METEO_API_KEY` in the environment. [Open-Meteo terms](https://open-meteo.com/en/terms)

Prepare the independent Iowa CSV using the acquisition command in the observations document, then evaluate the frozen artifact separately. [Field data acquisition](../../docs/moisture-observations.md)

```sh
uv run --locked python run.py evaluate_moisture --model ../../apps/api/src/field_shift/modules/geo_ml/artifacts/moisture-v1.json --observations data/observations-ifc.csv --cache data/archive --output data/iowa-fields-v1.json
```

## Use and scope

The eligible geography is restricted to the training region and locations within 180 km of a fitting station. A month is eligible when at least three unseen stations supply at least 30 complete holdout days in aggregate. The interval describes empirical cross-location errors. Individual conditions such as irrigation, local soil texture, and within-field variation can shift the true moisture outside that range. The estimate follows the 0.1° predictor grid and upstream publication delay. Use it to compare historical moisture and track regional conditions.

The next controlled experiment can add independent soil survey properties to test whether they explain differences in baseline bias across farms. Live USDA Soil Data Access queries returned texture, organic matter, drainage, bulk density, and water retention at the F-03 centroid and at all three Iowa probes. The surveyed dominant surface soil at the corn probe contains 36% sand and 21% clay. Both soybean probes have a dominant surface soil with 2% sand and 34% clay. For repeated inference, cache the soil survey snapshot by field geometry and survey version. Compare the enriched model on newly reserved farms because the current validation errors are known. Irrigation records and matched field sensors would allow separate validation for irrigated fields. [USDA Soil Data Access](https://sdmdataaccess.sc.egov.usda.gov/WebServiceHelp.aspx)

## Historical v2 with SSURGO

`fieldshift-vwc-a91e5897a6c2` estimates the correction to ERA5-Land using a regularized linear model. It adds SSURGO texture, organic matter, bulk density and the water contents at 33 and 1,500 kPa at the survey horizon covering 5 cm. Each cross-validation fold learns its own imputation medians. The fitted model uses 26,453 station-days and separate calibration uses 6,181 station-days. The model and its interval were frozen before opening the new field observations. [Release protocol](v2-protocol.md) · [New site manifest](src/field_shift_ml/evaluations/iowa-v2-farm-sites.json) · [USDA soil data](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo)

| Independent 2025 comparison | Locations | Paired days | ERA5-Land MAE / RMSE | v1 MAE / RMSE | v2 MAE / RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| New Iowa corn and soybean fields | 7 | 1,680 | 0.05672 / 0.07365 | 0.07922 / 0.09700 | 0.05681 / 0.07035 |
| New fields in v2's serving radius and months | 4 | 1,026 | 0.05323 / 0.06690 | 0.08579 / 0.10514 | 0.05797 / 0.07290 |
| Prior regional diagnostic | 5 | 1,209 | 0.11152 / 0.12344 | 0.05271 / 0.06241 | 0.10494 / 0.14320 |

Errors are in m³/m³. The eighth registered field had 24 thawed pairs and missed the 30-day threshold. Its score is reported separately. Across the seven qualified fields, v2 reduced RMSE by 4.5% relative to ERA5-Land and slightly increased MAE. The 90% target interval covered 91.0% of those paired observations. The in-coverage and regional comparisons support retaining ERA5-Land as the operational historical layer. [Independent farm report](src/field_shift_ml/evaluations/iowa-fields-v2.json)

From `packages/field-shift-ml`, prepare the 2021–2025 NOAA/USDA inputs, the v1 Iowa observations for the development soil snapshot, and the eight sealed v2 stations as described in the observations guide. The first phase receives station metadata only for the new farms. The second consumes their measurements and checks that the manifest matches the frozen model. Keep both SSURGO snapshots and the predictor cache to reproduce this dataset vintage. [Observation and SSURGO acquisition](../../docs/moisture-observations.md)

```sh
uv run --locked python run.py fetch_soil_profiles --stations data/observations.csv --stations data/observations-ifc.csv --cache data/ssurgo-cache --output data/soil-profiles-development.json
uv run --locked python run.py fetch_soil_profiles --stations data/observations.csv --stations data/observations-ifc.csv --stations data/observations-ifc-v2.csv --cache data/ssurgo-cache --output data/soil-profiles-v2.json
uv run --locked python run.py train_moisture_v2 fit --manifest src/field_shift_ml/evaluations/iowa-v2-farm-sites.json --soil data/soil-profiles-development.json --observations data/observations.csv --cache data/archive --output data/moisture-v2-frozen.json
uv run --locked python run.py train_moisture_v2 evaluate --manifest src/field_shift_ml/evaluations/iowa-v2-farm-sites.json --soil data/soil-profiles-v2.json --observations data/observations-ifc-v2.csv --cache data/archive --frozen data/moisture-v2-frozen.json --output data/moisture-v2.json --report data/iowa-fields-v2.json
```

## Expanded model searches

The offline runs use distinct targets, geographically held-out folds, and separate release checks. Each protocol records predictors, model families, hyperparameters, data limitations and reproduction commands.

- [Historical surface moisture](historical-surface-search.md)
- [Five-day surface forecast](forecast-protocol.md)
- [Root-zone water and stress](root-zone-search-protocol.md)
- [Maize rotation effects](rotation-yield-protocol.md)
