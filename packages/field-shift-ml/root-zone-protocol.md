# Root-zone water forecast release protocol

Target the water stored in the top meter of soil and report it as a standard available-water reference. Interpolate each profile linearly between valid USDA SCAN probes at 2, 4, 8, 20 and 40 inches. Extend the nearest reading from the shallowest probe to the surface, integrate to 1 m and average hourly profiles over a UTC day. Each daily value requires at least 20 valid concurrent profiles spanning all four six-hour blocks. Apply the same reference field capacity and wilting point to every benchmark prediction and observation. The resulting reserve in millimeters is a standardized proxy. Local plant-available water also depends on the farm's soil and crop.

The input is a single archived 00 UTC ECMWF IFS 50R1 run. Integrate its issue-day soil layers at 0–7, 7–28 and 28–100 cm. The physical comparator applies the current FAO-56 bucket with fixed maize reference Kc and depletion fraction, loam thresholds and no irrigation, driven by the same run's liquid rain and reference ET₀. Its first valid date is one UTC day after issue. Also compare the independent IFS layer forecast for each lead under the same reserve conversion. All dynamic features come from that one IFS run and its physical-bucket replay. [IFS Single Runs API](https://open-meteo.com/en/docs/single-runs-api)

The selected site roles and dates are locked in [the site manifest](src/field_shift_ml/evaluations/root-zone-sites.json). Reserve the whole named agricultural stations in the evaluation group. Exclude any development stations sharing a 2° block or lying within 40 km of evaluation and calibration sites. Fit and calibrate on separate sites during May–August 2026, then test during August–October on the reserved farms. Sample each Monday run at leads 1–5. Reject frozen-day forecasts with modeled shallow soil temperatures below 2 °C. Report site exclusions for availability and minimum paired days.

Fit a single standardized, station-weighted ridge regression on the physical residual and clip the result to the reference bucket capacity. Use 40 for the ridge penalty and shrink the residual correction by half. Estimate a 90% residual interval by lead with the calibration sites.

Promote only with at least three independent farms at 30 or more paired days, MAE and RMSE each at least 5% below the physical reference, a lower station-weighted RMSE than direct IFS layers, and empirical interval coverage of at least 75%. No farm or lead may degrade by over 10% in RMSE relative to the physical reference. Evaluation also needs field records of actual crop depth and irrigation to support a correction for a crop-managed production reserve. SCAN publishes sensor and station metadata. Independent farm field records and crop management have their own provenance.

## Observation snapshot

The snapshot contains 1,127 complete daily profiles. Eastview Farm, Tennessee supplied 75 days and Stanley Farm, Alabama supplied 73 days. Across those two sites the observed total storage ranged from 250.4 to 370.6 mm over 1 m. On 112 of the 148 observed days, storage reached or exceeded the benchmark's 300 mm reference field capacity. This limits how much the available-water proxy can distinguish wet conditions at those sites.

Johnson Farm, UAPB Lonoke Farm, Isbell Farms and Rock Springs supplied zero complete five-probe days in the specified test window. The original six-site cohort remains in the report. USDA's sites provide point measurements and may differ from managed crop conditions in a nearby field. [USDA Soil Climate Analysis Network](https://www.nrcs.usda.gov/resources/data-and-reports/soil-climate-analysis-network) · [AWDB documentation](https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html)

## Recorded comparison

The ridge model `fieldshift-root-79283eb91fa5` fitted 521 paired lead–day values across nine sites. Its separate calibration cohort contributed 195 pairs across three sites. The two qualified independent farms contributed 109 pairs. The metrics below weight the two farms equally.

| Method | MAE (mm) | RMSE (mm) |
| --- | ---: | ---: |
| FAO-56 reference bucket | 27.77 | 31.60 |
| Learned correction | 18.03 | 20.70 |
| Direct IFS layers | 16.61 | 19.03 |

| Lead (UTC days) | FAO-56 RMSE (mm) | Learned RMSE (mm) | Direct IFS RMSE (mm) |
| ---: | ---: | ---: | ---: |
| 1 | 22.55 | 15.66 | 18.40 |
| 2 | 27.08 | 17.89 | 19.05 |
| 3 | 30.79 | 19.77 | 18.99 |
| 4 | 35.10 | 22.17 | 19.64 |
| 5 | 39.97 | 26.62 | 19.04 |

The correction improved both metrics against FAO-56, with a 34.5% reduction in aggregate RMSE. Direct IFS performed better overall, the two qualifying farms fell short of the three-farm minimum, and crop depth and irrigation records were absent. Calibration half-widths span about 101–105 mm before clipping to the 150 mm bucket. They covered every paired held-out observation and are too wide to establish a precise farm-level estimate. The correction stays experimental and the Water forecast uses the physical bucket.

## Iteration with uncensored 0–1 m storage

Use the nine original fitting sites for candidate selection. Hold one entire site out of each fold and exclude any training sites within 40 km or the same 2° block. The original calibration sites are a separate diagnostic, and the Eastview and Stanley holdout is excluded from this iteration. Every comparison uses the same archived IFS issue and lead as the physical bucket. Report both the standardized reserve and total soil-water storage before subtracting the reference wilting point or clipping at field capacity. The reserve hit one of its bounds for 377 of 521 development pairs. FAO-56 forecasts available reserve, so it has no total-storage metric.

Compare two fixed blends with FAO-56, a half-shrunk IFS bias adjustment, and a three-feature IFS ridge. The ridge uses forecast storage, initial storage and shallow soil temperature, with standardized inputs, station weighting, a penalty of 100 and residual shrinkage by half. Repeat with that correction tapered linearly to zero at 150 and 300 km from the nearest fitting station. The development gate requires at least 5% improvement over direct IFS on reserve MAE and RMSE, no worse total-storage MAE or RMSE, and at most 10% reserve RMSE degradation in any held site or lead.

| Method | Development reserve MAE (mm) | Development reserve RMSE (mm) | Development total RMSE (mm) | Calibration reserve MAE (mm) | Calibration reserve RMSE (mm) |
| --- | ---: | ---: | ---: | ---: | ---: |
| FAO-56 | 87.13 | 99.44 | — | 45.00 | 59.03 |
| Direct IFS | 84.02 | 98.29 | 173.43 | 50.04 | 63.73 |
| IFS with 25% FAO-56 | 84.76 | 98.60 | 173.93 | 48.61 | 62.38 |
| IFS with 50% FAO-56 | 85.52 | 98.88 | 174.57 | 47.28 | 61.13 |
| IFS bias correction | 56.24 | 71.62 | 149.06 | 80.54 | 87.87 |
| IFS ridge | 42.92 | 58.49 | 136.32 | 81.75 | 91.56 |
| IFS ridge, 150 km taper | 81.97 | 96.12 | 171.37 | 50.04 | 63.73 |
| IFS ridge, 300 km taper | 65.09 | 76.68 | 152.39 | 46.02 | 56.04 |
| Original FAO-56 ridge refit per fold | 60.87 | 68.00 | — | 60.37 | 67.59 |

Only the 300 km taper passed the development gate. The untapered ridge raised RMSE at Glacial Ridge, Minnesota from 98.71 to 139.76 mm. The 300 km taper reverted to direct IFS there. On the 195 calibration pairs the selected ridge lowered RMSE versus both baselines, while FAO-56 had lower MAE. The taper is a research candidate with three inputs and no extra provider call. Its radius is a modeling choice, and the production artifact's 150 km limit remains in force.

The [extrapolation protocol](src/field_shift_ml/evaluations/root-zone-extrapolation-sites.json) reserved eight additional named USDA SCAN farm and research sites. Active SCAN stations with a 40-inch element within the 150 km serving footprint failed separation from the original development or test geography. The eight reserved sites were 193.9–1569.5 km from the nearest fitting site and returned zero complete five-probe days in the locked August–October window. The runner records their availability without downloading IFS runs for sites below the minimum observation count. [USDA SCAN](https://www.nrcs.usda.gov/resources/data-and-reports/soil-climate-analysis-network)

NOAA USCRN supplies independent hourly probes at 5, 10, 20, 50 and 100 cm. Of its stations within 150 km of fitting sites, only Goodridge and Chillicothe passed the 40 km and 2° geography filters. Goodridge is at Agassiz National Wildlife Refuge. Chillicothe is at the University of Missouri Forage Systems Research Station, 64.3 km from the closest fitting site. It supplied 75 complete UTC days and 55 pairs with archived IFS leads. Iowa State's four stations listed with deep SoilVue probes shared a prior block or lay inside the 40 km buffer. [NOAA hourly product](https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/README.txt) · [NOAA station siting](https://www.ncei.noaa.gov/access/crn/pdf/StationsByState_LowRes.pdf) · [Iowa State soil sensors](https://www.mesonet.agron.iastate.edu/agclimate/hist/hourly.php)

| Method | Chillicothe reserve MAE (mm) | Chillicothe reserve RMSE (mm) | Chillicothe total RMSE (mm) |
| --- | ---: | ---: | ---: |
| FAO-56 | 114.96 | 116.77 | — |
| Direct IFS | 112.97 | 115.27 | 230.66 |
| IFS ridge, 300 km taper | 54.08 | 55.67 | 173.22 |

The single-site diagnostic improved both benchmarks but still underestimated reserve by 54.08 mm on average. The NOAA probes are at a fenced climate station. Verified crop depth and irrigation records for the probe plot are absent. The independent managed-farm release requirement is still unmet, so the candidate stays offline. The [evaluation report](src/field_shift_ml/evaluations/root-zone-research.json) includes paired counts, errors by site and lead, and the observation-source hashes.

## Reproduction

The [expanded Water search](root-zone-search-protocol.md) covers survey soils, weather and forecast trajectories across multiple model families with nested station validation.

Run from `packages/field-shift-ml` with the [training dependencies](requirements.txt) installed.

```sh
uv run --locked python run.py fetch_root_zone_observations --cache data/root-zone-scan --output data/root-zone.csv
uv run --locked python run.py train_root_zone --observations data/root-zone.csv --cache data/root-zone-ifs --output data/root-zone-model.json
uv run --locked python run.py research_root_zone --observations data/root-zone.csv --weather-cache data/root-zone-ifs --scan-cache data/root-zone-scan --noaa-cache data/root-zone-noaa --output data/root-zone-research.json
```

The data export saves the predeclared site manifest hash and per-request source hashes alongside the CSV. Reusing the original response cache reproduces the archived inputs. Source revisions may change a later download.
