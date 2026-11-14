# Water model and feature search

The target is measured water storage through 1 m. The benchmark also converts it to the standard reference reserve used in the existing protocol. It evaluates both targets because many observations reach a reserve bound. A model can look accurate on the clipped reserve while missing the measured amount of water. [Root-zone target and release criteria](root-zone-protocol.md)

## Features available when the forecast is issued

| Group | Agronomic rationale and predictors |
| --- | --- |
| Compact IFS core | The initial and forecast 1 m storage represent current and predicted water supply. Shallow soil temperature reflects seasonal and near-surface conditions. |
| Soil moisture layers | Initial and forecast moisture gradients show differences between the topsoil, mid-layer and subsoil. Changes since initialization measure wetting or drying by depth. |
| Forecast weather | Rain and reference evapotranspiration represent water input and atmospheric demand. Include daily values, running totals, their difference, air and soil temperature contrast, peak rain, wet days and days since rain. |
| Forecast trajectory | Lead, day-to-day change, change since initialization, path extremes and cumulative variation distinguish stable storage from rapid wetting or drying. |
| Physical water balance | Include reference reserve, water uptake, cumulative drainage, remaining storage capacity, the difference between IFS and FAO-56, unmet potential crop use and rain above opening capacity. |
| Season and geography | Encode the day of year cyclically. Evaluate coordinates as a separate group so their contribution can be measured. The distance taper uses only fitting station coordinates. |
| Surveyed root-zone soil | Integrate texture, organic matter, bulk density, available water capacity and saturated conductivity across representative SSURGO horizons down to 1 m. Add the subsoil clay contrast, depth coverage, slope, drainage and cumulative water demand relative to survey capacity. |
| Antecedent archive | Use the most recent ERA5 day permitted by the six-day archive cutoff. Test 7- and 28-day root-zone storage, changes from the current forecast, past rain less ET₀ and dry-spell duration when archive inputs are available. |

Every IFS trajectory uses the same pinned run and ends at the evaluated lead. The soil snapshot uses the dominant eligible component at each point. Overlapping transitional horizons contribute each depth interval once. Attributes with less than 70% depth coverage stay missing. The water-capacity feature scales the available representative values to the 1 m reference depth. Soil properties describe map-unit components and are separate from measured crop and irrigation records. [FAO-56 water balance](https://www.fao.org/4/X0490E/x0490e0e.htm) · [USDA SSURGO](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo) · [ERA5 archive](https://open-meteo.com/en/docs/historical-weather-api)

The development dataset has no consistently verified issue-time irrigation, crop stage, root-depth or field canopy observations across the nine stations. Those inputs could change crop demand and local storage. This benchmark keeps the common maize reference and records soil and weather features separately. The archive provider reached its daily request limit during this run. The antecedent feature builder is tested for its issue-time cutoff, and the recorded comparison excludes that optional input.

## Model and parameter search

The grid includes a station-balanced bias correction, ridge fit to the IFS error, ridge fit directly to storage, elastic net, Huber regression, RBF kernel ridge, ExtraTrees, random forest and histogram gradient boosting. Ridge at the common regularization value covers every subset of available optional groups added to the three-feature core. Additional ridge regularization values use the core, single-group extensions, all-group and leave-one-group-out bundles. The other model families use four predetermined feature bundles. The grid varies regularization, sparsity, Huber loss cutoff, kernel bandwidth, tree depth and minimum leaf size, or boosting rate, leaf count and regularization as appropriate. Each fit is evaluated with three correction strengths and four support radii, including an untapered correction.

The inner and outer folds both hold out entire stations. Each fitting fold excludes stations within 40 km of the held-out station or in its 2° block. The inner folds choose the full configuration and the outer folds measure the selection procedure. Each fold estimates imputation and scaling using its own fitting rows. Constant or entirely missing fitting features have zero weight in the transformed data. Each station receives the same total weight. The final configuration is chosen using development folds only. A separate report scores it on the previously used calibration sites.

A candidate must lower both reference-reserve MAE and RMSE by at least 5% against raw IFS, preserve or improve both total-storage metrics and limit reserve RMSE regressions to 10% per station and lead. Eligible candidates are ranked by average baseline-relative MAE and RMSE across both targets. Models within 1% of the lowest error score are ranked by extra data needs, model family, feature count and use of a bounded correction. Direct IFS is the fallback when a fold has no eligible candidate. The report also compares each family and each feature group with and without that group under the same development folds.

The prior NOAA and independent USDA farm results have already been examined in earlier iterations. The new search does not use those observations for selection or claim a new independent managed-farm release. FAO-56 remains the Water forecast in the application.

## Broad search results

The measured search used 48 features across eight groups and SSURGO profiles for all nine fitting and three calibration stations. It evaluated 128 ridge feature bundles and 3,564 model and correction configurations. The full run took 279 seconds on one computation thread. The final development selection was a ridge residual correction with alpha 10, the three core features and the soil group, full correction strength and no geographic taper. The pooled development RMSE was 22.98 mm for the selected configuration and 39.64 mm for the best configuration without the soil group. Those two scores include configuration selection on the same development folds.

| Evaluation | FAO-56 reserve MAE / RMSE (mm) | Direct IFS reserve MAE / RMSE (mm) | Previous compact ridge MAE / RMSE (mm) | New selection MAE / RMSE (mm) |
| --- | ---: | ---: | ---: | ---: |
| Nested procedure, 521 pairs from nine development stations | 87.13 / 99.44 | 84.02 / 98.29 | 65.09 / 76.68 | 23.86 / 50.03 |
| Locked development winner on the three previously used calibration sites, 195 pairs | 45.00 / 59.03 | 50.04 / 63.73 | 46.02 / 56.04 | 58.02 / 72.57 |

The nested procedure reduced total-storage RMSE from 173.43 mm for direct IFS and 152.39 mm for the previous compact ridge to 115.34 mm. It also missed all 71 observed reference stress cases, compared with 44 missed by FAO-56 and 52 by direct IFS. It raised no false alarms, compared with 269 and 258 respectively. On the previously used calibration sites, the selected model overestimated reference reserve by 46.56 mm on average and missed 65 of 129 reference stress cases. This makes the lower nested regression error unsuitable as the sole selection criterion for Water. [Broad search report](src/field_shift_ml/evaluations/root-zone-search.json)

## Stress-sensitive follow-up

After the broad benchmark exposed the missed stress cases, a follow-up re-evaluated 2,316 of the bias and ridge configurations in 27 seconds on one thread. It added an inner validation requirement to match or improve FAO-56 on both missed cases and false alarms at the 67.5 mm reference maize stress threshold. The regression and station safeguards still apply. The follow-up reuses the same development cohort and is a diagnostic.

| Nested development procedure | Reserve MAE / RMSE (mm) | Total storage RMSE (mm) | Missed stress out of 71 cases | False alarms out of 450 other pairs |
| --- | ---: | ---: | ---: | ---: |
| FAO-56 | 87.13 / 99.44 | — | 44 | 269 |
| Direct IFS | 84.02 / 98.29 | 173.43 | 52 | 258 |
| Broad regression search | 23.86 / 50.03 | 115.34 | 71 | 0 |
| Stress-sensitive search | 33.99 / 54.42 | 110.10 | 52 | 85 |

Only five configurations met the follow-up gates on the pooled development folds. Four of nine outer folds had no eligible inner candidate and reverted to direct IFS. The complete development set again selected the core-plus-soil ridge with alpha 10. Its calibration error and missed stress cases therefore remain the same as in the broad search. The stress-sensitive selection caught 19 more observed cases than the broad procedure on the outer folds and missed eight more cases than FAO-56. These outcomes support keeping the physical forecast in the application. [Stress follow-up report](src/field_shift_ml/evaluations/root-zone-stress-search.json)

## Reproduction

Use the frozen SCAN observations and IFS archive from the [original protocol](root-zone-protocol.md) and the offline Python requirements. Run from `packages/field-shift-ml`.

```sh
uv run --locked python run.py root_zone_soil --observations data/root-zone.csv --cache data/root-zone-ssurgo --output data/root-zone-soil.json
uv run --locked python run.py search_root_zone --observations data/root-zone.csv --weather-cache data/root-zone-ifs --soil-snapshot data/root-zone-soil.json --output data/root-zone-search.json
uv run --locked python run.py search_root_zone --observations data/root-zone.csv --weather-cache data/root-zone-ifs --soil-snapshot data/root-zone-soil.json --stress-gate --output data/root-zone-stress-search.json
```

To supply the optional archive group, build its snapshot with `uv run --locked python run.py root_zone_history` using the same observations and a separate cache and output path. Pass that output to the search as `--history-snapshot`. Source responses are cached and hashed. The report also hashes the parsed IFS runs and their paired leads in canonical station and issue order. Fitting is limited to one computation thread. The same trained estimator is reused for all strength and radius combinations and for validation rows sharing the same fitting set.
