# Soil moisture forecast evaluation

The target is the observed UTC daily mean volumetric water content at about 5 cm. The only weather inputs are predictions from one archived ECMWF IFS HRES run initialized at 00 UTC. Training and serving use the full UTC days at leads 1 through 5. The forecast for the current date uses yesterday's 00 UTC initialization.

Use weekly Monday initializations from April through mid-November 2024 for fitting and calibration. Reuse the geographically separated fitting and calibration locations already pinned in the v1 artifact, and group-weight the residual fit equally per station. Fit one ridge model with regularization 40 and shrink its residual correction by half. Calibrate one 90th-percentile absolute error width per lead with the separate calibration locations. Freeze the fit before scoring.

The primary benchmark uses the new farms pinned for the soil survey experiment, each at least 40 km from forecast fitting and calibration sites. Use the 2025 growing-season window and a seven-day sampling cadence. Compare each lead against the uncorrected prediction from the very same IFS run. Require at least three unseen farms, at least 30 paired days at each farm, an improvement of at least 5% in both overall station-balanced MAE and RMSE, empirical interval coverage of at least 75%, and no lead or farm with more than 10% deterioration in RMSE. Compute and report every lead separately. Keep raw IFS as the serving forecast whenever the learned candidate fails the gate.

The 2024–2025 data are from the 49R1 archive, which includes retrospective hindcasts. The documented operational source switched to 50R1 in May 2026. A serving correction requires a passing benchmark on the same IFS cycle. [ECMWF cycle and archive coverage](https://open-meteo.com/en/docs/single-runs-api)

ERA5-Land becomes available after the forecast dates. It serves as the historical layer and the retrospective baseline for the separate historical experiment.

## Recorded comparison

Candidate `fieldshift-ifs-9610bf996164` fitted 4,053 paired days from 29 sites and calibrated its intervals with 1,104 days from seven separate sites. The sealed test set contributed 1,048 paired days across eight agricultural sites. One site supplied two valid pairs in this forecast sampling window.

| Station-balanced metric | Raw IFS 49R1 | Learned correction |
| --- | ---: | ---: |
| MAE (m³/m³) | 0.08272 | 0.07961 |
| RMSE (m³/m³) | 0.10458 | 0.09729 |

The MAE improvement was 3.8% and the interval covered 82.5% of the held-out observations with equal site weight. The 5% MAE threshold, farm coverage gate, and current-cycle validation gate failed. The bundled artifact retains every lead and farm score. The live layer serves raw IFS 50R1.

Run from `packages/field-shift-ml` after installing the pinned [training dependencies](requirements.txt) and preparing the two [observation snapshots](../../docs/moisture-observations.md).

```sh
uv run --locked python run.py train_moisture_forecast --observations data/observations.csv \
  --new-farms data/observations-ifc-v2.csv --cache data/ifs-runs \
  --output data/moisture-ifs-v1.json
```

The public Iowa Flood Center snapshot ends in April 2026, before the operational cycle switch. Keep a distinct post-upgrade field evaluation before enabling a live correction. [Iowa Flood Center dataset](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/)

## Expanded geographic search

Use the same 00 UTC issue times and 5 cm observation target. Hold out whole 2° blocks in five outer folds and remove training stations within 40 km of any evaluation station. Repeat model and parameter selection in three geographically buffered inner folds. Weight stations equally and fit both imputation and scaling on each fold's training rows. Pool outer-fold predictions to score the selection procedure. Select the final configuration on five folds within the 29 original training stations. Keep the seven calibration sites and all farm observations outside selection. Rank by the mean of MAE and RMSE normalized to raw IFS. Prefer the smaller configuration when its score is within 1% of the best.

The search compares raw IFS, persistence, a station-balanced bias, ridge, elastic net, Huber regression, quadratic ridge, histogram gradient boosting, and extremely randomized trees. The 149 configurations vary feature bundles, regularization, residual shrinkage, leaf size, tree depth and complexity, and fitting a residual or the moisture target directly. The runner limits numerical libraries and ensembles to one thread, reads cached forecasts, and fits each estimator once per fold for all shrinkage settings.

| Feature family | Inputs and physical interpretation |
| --- | --- |
| Existing IFS | Surface and 7–28 cm moisture, issue-day surface state, temperatures, precipitation and ET₀ totals, forecast lead and seasonal cycle |
| Layer trajectory | Surface-to-subsurface gradient, daily wetting and drying, changes from initialization, and the issued trajectory's range and mean through the target lead |
| Precipitation and drying | Daily and exponentially weighted recent precipitation and ET₀, cumulative precipitation minus ET₀, time since precipitation and soil–air temperature difference |
| Static soil | USDA SSURGO texture, organic matter, bulk density, drainage, water at 33 and 1,500 kPa, available water capacity, and interactions between soil properties and the forecast state |

All dynamic values come from the pinned IFS run. No in-situ values after issuance or future imagery enter the predictors. The soil survey is a static mapped property snapshot and may contain revisions published after the hindcast date. The surface archive contains total precipitation, including snow, so the derived precipitation features describe a wetting proxy. Liquid rain, deeper layers, radiation, humidity, and wind were considered as additional groups. The archive returned HTTP 429 for fresh requests during the experiment. They require comparable archived values before inclusion. Satellite vegetation, crop management, irrigation, and terrain were absent from the comparable training and evaluation snapshots. [Open-Meteo Single Runs archive](https://open-meteo.com/en/docs/single-runs-api) · [USDA soil hydraulic definitions](https://www.nrcs.usda.gov/sites/default/files/2022-09/SSM-ch8.pdf)

The final candidate is full-strength ridge with alpha 400 and 28 inputs from the existing IFS, layer trajectory, and static soil groups. The full feature bundle improved the normalized inner score by less than 1%, so the simpler candidate won. With model and parameters fixed, adding SSURGO to the existing IFS features moved tuning-fold RMSE from 0.11533 to 0.08494 m³/m³. Adding layer trajectory brought it to 0.08476. The strongest bundle without static soil used boosted trees and reached 0.10926. These feature ablations reuse the tuning folds. The nested results below estimate the search procedure on held-out blocks.

| Comparison in m³/m³ | Raw IFS MAE / RMSE | Prior ridge MAE / RMSE | Expanded model MAE / RMSE |
| --- | ---: | ---: | ---: |
| 4,053 pairs, 29 sites in nested 2024 geographic evaluation | 0.10695 / 0.13298 | 0.09680 / 0.11892 | 0.07844 / 0.09687 |
| 1,048 pairs, eight previously scored 2025 IFC sites | 0.08272 / 0.10458 | 0.07961 / 0.09729 | 0.06370 / 0.08008 |

The selected model's interval covered 91.3% of the known 2025 farm observations. Those farm labels have appeared in previous reports and establish continuity with earlier work. One site still supplied only two qualifying pairs.

The post-upgrade [research-station manifest](src/field_shift_ml/evaluations/surface-forecast-50r1-sites.json) fixes locations and the May–October 2026 issue window independently of their moisture outcomes. It retains quality-controlled hourly 5 cm readings at NOAA stations and 2-inch readings flagged valid by USDA. It records archive gaps and separates results for sites within 180 km of the fitting geography. An exact archived issue from Water can supply the same surface variables. Forecast coverage at these sites measures transfer to 50R1. The network metadata do not establish that these probes are in managed crop fields. [NOAA hourly observations](https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/) · [USDA AWDB](https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html) · [ECMWF upgrade chronology](https://www.ecmwf.int/en/forecasts/documentation-and-support/changes-ecmwf-model)

NOAA supplied 488 complete days at four stations. The USDA station supplied zero quality-controlled days. Eleven cached 50R1 runs supplied 55 verification pairs at Chillicothe, the only in-support NOAA station. Its separate 1 m profile has appeared in Water research, and its 5 cm observations first enter a surface forecast evaluation here. None of the other NOAA stations had exact cached issues, and the public archive's quota blocked new requests. On the 55 pairs, raw 50R1 had 0.10433 MAE and 0.12028 RMSE. The selected model had **0.07647 MAE and 0.09247 RMSE**, with 85.5% interval coverage and a remaining −0.06772 m³/m³ average bias. The evidence is limited to one research station. The live layer uses raw 50R1.

Run from `packages/field-shift-ml` with the pinned training dependencies. The supplied weather paths may point to existing local archive caches.

```sh
uv run --locked python run.py fetch_surface_forecast_observations \
  --cache data/surface-50r1-raw --output data/surface-50r1-observations.csv

uv run --locked python run.py fetch_soil_profiles \
  --stations data/surface-50r1-observations.csv --cache data/ssurgo \
  --output data/surface-50r1-soils.json

uv run --locked python run.py research_surface_forecast \
  --observations data/observations.csv --known-farms data/observations-ifc-v2.csv \
  --soils data/soil-profiles-development.json --known-farm-soils data/soil-profiles-v2.json \
  --postcycle-observations data/surface-50r1-observations.csv \
  --postcycle-soils data/surface-50r1-soils.json --postcycle-root-cache data/root-zone-ifs \
  --cache data/ifs-runs --output data/surface-forecast-search.json
```

Add `--download-postcycle-weather` once the provider permits fetching the missing batches. The runner otherwise uses exact cached requests and reports missing issues. The [search report](src/field_shift_ml/evaluations/surface-forecast-search.json) contains all candidates, feature ablations, source digests, spatial folds, and results by station and forecast lead.
