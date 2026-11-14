# Maize rotation yield effect

The target is the difference in maize grain yield between a rotation treatment and the continuous-maize control in the same trial, year, and management stratum. It is expressed in tonnes per hectare at the source standard of 15.5% grain moisture. Trial outcomes support historical treatment analogues for planning. Absolute field yield and the aggregate yield of a three-year plan require additional measurements.

Use the public [USDA Sustainable Corn CAP workbook](https://doi.org/10.15482/USDA.ADC/1411953). Use 2012–2015, the years with an actual previous-crop record. Keep the continuous maize control and the unamended maize–soybean, maize–soybean–wheat and maize–wheat–soybean treatments. Keep the recommended nitrogen treatment and match tillage, drainage and landscape position. Exclude known subirrigation. Use one estimate for each site, year and preceding crop after averaging matched management strata.

The independent test sites are Hoytville, the Southeast Research Farm, Orr and Marshfield. Their coordinates were selected from treatment availability before analyzing yield values. Lancaster and Freeman are interval-calibration sites. Keep both Wooster experiments in the same training farm group. All calibration and test groups must be at least 50 km from fitting sites, and test groups must be at least 50 km from calibration sites. Fit a single ridge model (alpha 6) using the previous crop, centered coordinates and their interaction. Compare against the training farm-balanced mean effect for each preceding crop. Calibration uses the 90th percentile of the absolute errors of geographically separate observations. Each evaluation farm has equal weight regardless of how many trial years it contributes.

Promotion requires at least three independent test farms and at least two farms with six matched site-years overall for each preceding crop. MAE and RMSE must both improve by 5% relative to the training mean baseline. Neither crop-specific RMSE may deteriorate by more than 10%. Observed interval coverage must be at least 70%. The result remains experimental if any condition fails.

The runtime uses a supported maize-after-soybean or maize-after-wheat transition from the actual current crop and planned sequence. The geography is restricted to the documented U.S. Corn Belt rectangle and 200 km from a training, calibration or test site with matched observations. The app can show a separate continuous-maize reference only when maize is already present and the entire plan continues maize. Soybean and wheat yields do not enter this comparison.

## Recorded validation

The workbook contains multiple crops and locations. After requiring an observed predecessor, a continuous-maize control and the same management, it contributes **59 site-year-predecessor contrasts** from **101 matched management strata**. The training, calibration and independent test sets contain four, two and four distinct research farms, respectively. The minimum cross-split distance is 69.78 km. The test set contains 24 contrasts across Hoytville, Marshfield, Orr and Southeast Research Farm.

| Farm-balanced error on unseen farms | Training means by predecessor | Learned candidate |
| --- | ---: | ---: |
| MAE (t/ha) | 1.0720 | 1.1884 |
| RMSE (t/ha) | 1.5841 | 1.6056 |

The calibrated interval covered 90.625% of the test contrasts with equal farm weight. The learned candidate failed the MAE and RMSE thresholds. The artifact records the comparison for each farm and each predecessor. The current Water-based rotation scenario comparison remains the production method. The reference estimate describes the mean contrast in matched field trials, conditional on the supported crop and site. The data represent historical research plots and their prescribed management.

## Reproduce

The source is [Sustainable Corn CAP Research Data, USDA Ag Data Commons](https://doi.org/10.15482/USDA.ADC/1411953), published by Abendroth and colleagues under CC0. The pipeline reads the plot identifiers, agronomic yields and site metadata from the pinned workbook and verifies its SHA-256. Blank and nonnumeric yields are excluded. The maize grain column is in kilograms per hectare. The trainer converts to tonnes per hectare and rejects values outside 0–25 t/ha.

After installing the offline [training requirements](requirements.txt), run from `packages/field-shift-ml`. The trainer fetches the workbook if it is absent at the specified path.

```sh
uv run --locked python run.py train_rotation_yield --workbook data/cscap.xlsx --output data/rotation-yield-v1.json
```

The resulting JSON includes the source hash, model version, uncertainty and the release decision. The checked-in candidate is `fieldshift-rotation-aa977d6f5072`.

## Iteration protocol

Use the same outcome and retain the original fitting, calibration and test farm groups. Select variants exclusively within the four fitting farms with leave-one-farm-out folds. In each outer fold, repeat model selection inside the remaining farms and then score the selected model on the outer farm. This measures the selection procedure. Refit the final variant chosen by the leave-one-farm-out score across all four fitting farms. The four original test farms have published v1 results and serve as a regression diagnostic. Keep calibration farms separate and use them to estimate the candidate's 90th percentile absolute-error interval.

Compare the original farm-balanced mean per predecessor with the median of farm means, a pooled center, partial pooling with one overall mean as a pseudo-farm, and 25% and 50% blends of the original geographic ridge with the predecessor mean. Include the original ridge in the comparison. If a fitting fold lacks a predecessor, predict the overall farm-balanced mean for it. Rank by the mean of MAE and RMSE relative to the predecessor-mean baseline. When scores are within 1% of the minimum, choose the first variant in the listed order.

Use the University of Illinois [Morrow Plots v2 release](https://doi.org/10.13012/B2IDB-7865141_V2) as a fresh external diagnostic after final selection. Compare phase 5 maize in the maize–soybean rotation to continuous maize within the same year and subplot suffix under the LNPK fertility program, with equal recorded nitrogen rates. Require an observed soybean crop for the preceding year and numeric yields from both subplots. Average the matched subplot contrasts within each year. Convert maize bushels per acre using 56 pounds per bushel. Soil-test-based phosphorus and potassium rates can differ. Morrow Plots is one research location with rotation assigned by plot and provides no matched wheat contrast. Its yields can challenge the model, while promotion still requires the original minimum farm and predecessor coverage on fresh independent sites.

The [Zhou et al. replication table](https://doi.org/10.5281/zenodo.13901372) publishes experimental maize-after-soybean contrasts from the USDA and [Bowles et al.](https://doi.org/10.6078/D1H409) trials. Use only `NE` and `SD` for an additional extrapolation diagnostic. The other locations occur in, or are within 50 km of, the original USDA trials. Use the typical-nitrogen `SN` rows for Nebraska and the published low-nitrogen `LN` rows for South Dakota. Average the experimental t/ha contrasts by site and year. Both coordinates lie outside the current longitude boundary. South Dakota also differs in nitrogen treatment. Keep both sites out of the model fitting, calibration and release gates.

### Iteration results

The original ridge won the selection score across the four fitting farms. The nested procedure chose four different estimators across the four outer folds, indicating unstable model selection. The apparent gain for the chosen ridge in the selection score largely disappeared after nesting.

| Farm-balanced evaluation | Matched years | Mean baseline MAE | Ridge or selected MAE | Mean baseline RMSE | Ridge or selected RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| Nested selection across four fitting farms | 23 | 1.4728 | 1.4732 | 1.8316 | 1.8280 |
| Original four USDA test farms, regression diagnostic | 24 | 1.0720 | 1.1884 | 1.5841 | 1.6056 |
| Fresh Morrow Plots maize-after-soybean trial | 9 | 1.5963 | 1.5363 | 2.3134 | 2.1175 |
| Fresh Nebraska and South Dakota extrapolation | 29 | 0.9806 | 1.1938 | 1.2541 | 1.6093 |

All errors are in t/ha. The Morrow Plots comparison averages 27 subplot pairs and sits 224 km from the nearest USDA trial. Its MAE gain is 3.8%, below the 5% threshold, and it contributes one farm and zero wheat contrasts. The candidate remains experimental. The historical data support further evaluation within the U.S. Corn Belt. The production scenario uses the existing Water method.

The offline benchmark validates cached source digests and downloads only the missing workbook and small trial exports. The evaluation uses 59 original USDA contrasts, nine external Illinois years, and 29 extrapolation site-years. No scheduled retraining is required for these versioned historical releases. Run from `packages/field-shift-ml` using the offline training requirements.

```sh
uv run --locked python run.py iterate_rotation_yield \
  --workbook data/cscap.xlsx \
  --morrow data/morrowplots.csv \
  --zhou data/zhou-corn-experimental.csv \
  --output data/rotation-yield-iteration.json
```

The [evaluation report](src/field_shift_ml/evaluations/rotation-yield-iteration.json) includes the source hashes, individual candidate scores, farm coverage, release checks and per-site extrapolation results.

With the development dependencies installed, run the focused offline checks from `packages/field-shift-ml`.

```sh
uv run --locked pytest tests/test_iterate_rotation_yield.py
```

## Domain feature search

The larger search keeps the original maize yield contrast and whole-farm roles. It evaluates every subset of four feature groups with the known predecessor. The geography group contains latitude and longitude. The climate group summarizes April–May rainfall and frost, June–August rain, the longest summer dry spell, atmospheric drying demand and heat load, and May–September growing degree days. It uses Daymet from 1981 through 2005 so the reference window ends before the independent Kellogg seasons. Earlier observations in the reused diagnostics have no climate-group prediction. [Daymet V4 R1](https://doi.org/10.3334/ORNLDAAC/2129)

The soil group includes SSURGO surface sand, clay, organic matter, bulk density, available water fraction and natural drainage. Map-unit aggregates add available water storage through 1 m and between 25 cm and 1 m, minimum spring water-table depth and slope. These attributes describe mapped soils at the trial location. The soil snapshot may have been revised after the study. The plan group describes shares of matched trial strata using no-till, artificial drainage and a three-crop rotation. It relies on documented treatments and uses identical matching and yield aggregation to the original target. Current-season realized weather, final plant population, canopy sensing, harvest measurements, pests, and in-season fertilizer observations are excluded from planning predictors. The benchmark tests the recommended nitrogen management stratum. It does not estimate effects of changing a fertilizer dose. [SSURGO table metadata](https://sdmdataaccess.nrcs.usda.gov/documents/TablesAndColumnsReport.pdf) · [USDA trial data dictionary](https://datateam.agron.iastate.edu/cscap/dl/)

Compare the farm-balanced predecessor mean and the original geographic ridge with 723 configurations in total. The trainable families are ridge, elastic net, robust Huber regression, RBF kernel ridge, extremely randomized trees and shallow gradient boosting. The grids cover regularization, sparsity, robustness, kernel width, tree depth, minimum leaf size and boosting rate. Some ridge recipes add predecessor interactions and cross terms for drying demand by soil water storage and dry-spell length by tillage. The ridge grid also tests tapering the correction to zero at 150, 250 or 400 km from the nearest fitting site for the same predecessor.

Train residuals against the fold's predecessor mean, with farm-balanced weights. Learn medians, scaling and constant-column exclusions only from the fitting portion. Fall back to the reference when a required group or predecessor has no support. Outer folds leave out one entire farm. Inner folds retune inside the remaining farms. Both require at least 50 km between fitting and evaluation locations. The objective averages relative MAE and RMSE, preferring simpler configurations within 1% of the best score. Keep the two calibration farms apart from both selection and evaluation. The reported 90% intervals are empirical and come from those two farms.

The separate Living Field Lab diagnostic uses published Michigan State conventional, no-cover plots. It matches each harvested maize plot to its observed preceding crop on that same plot and to a continuous-maize control in the same year and replicate. Each contrast needs at least two replicates. Use the published 15.5% grain moisture basis. The conditions match the reported management category, with no verified pairwise match on individual nitrogen applications. The Kellogg data support internal analysis and require written permission for publication. [Kellogg trial data](https://lter.kbs.msu.edu/datatables/108) · [Yield protocol](https://lter.kbs.msu.edu/protocols/194) · [Terms of use](https://lter.kbs.msu.edu/data/terms-of-use/)

The Ohio Triplett–Van Doren release reuses Wooster and Hoytville geography. The public Elora CSV summarizes rotation yields across both maize phases, so it cannot identify the immediate predecessor. The Brookings trial has a corn–soybean control and no continuous-maize control. These sources do not contribute to this independent benchmark. [Ohio dataset](https://doi.org/10.5061/dryad.1zcrjdg4t) · [Elora dataset](https://doi.org/10.1371/journal.pone.0113261.s002) · [Brookings dataset](https://doi.org/10.15482/USDA.ADC/25383307)

Run from `packages/field-shift-ml` using the offline training dependencies. The command checks pinned yield-source hashes, records predictor checksums and caches the Daymet and SSURGO queries. The search uses one numerical thread and reuses repeated inner fits and untapered predictions.

```sh
uv run --locked python run.py search_rotation_yield \
  --workbook data/cscap.xlsx \
  --morrow data/morrowplots.csv \
  --zhou data/zhou-corn-experimental.csv \
  --kbs data/kbs-yield.csv \
  --climate-cache data/rotation-daymet \
  --soil-cache data/rotation-ssurgo \
  --output data/rotation-yield-domain-search.json
```

### Domain search results

All 22 grouped predictors were present on the 23 development contrasts. Independent replication is four fitting farms, with wheat represented at two of them. The final development selection chose ridge with predecessor-by-coordinate interactions and regularization 0.3. Inner selection instead chose four different configurations across the four outer folds. Removing the climate group improved nested RMSE from 1.857 to 1.814 t/ha while raising MAE above the baseline. The soil-only, climate-only and plan-only searches each performed worse than the baseline in nested MAE and RMSE. The complete [research report](src/field_shift_ml/evaluations/rotation-yield-domain-search.json) contains the single-group searches, the leave-one-group-out searches, per-farm outcomes and all configuration scores.

| Evaluation | Matched site-years | Predecessor mean MAE / RMSE | Domain search MAE / RMSE |
| --- | ---: | ---: | ---: |
| Nested selection on four fitting farms | 23 | 1.473 / 1.832 | 1.558 / 1.857 |
| Original USDA test farms, reused | 24 | 1.072 / 1.584 | 1.628 / 1.991 |
| Morrow Plots, reused | 9 | 1.596 / 2.313 | 1.467 / 1.897 |
| Nebraska and South Dakota extrapolation, reused | 29 | 0.981 / 1.254 | 1.580 / 2.050 |
| Kellogg Living Field Lab, new site | 6 | 1.945 / 2.080 | 1.142 / 1.223 |

Errors are in t/ha. Kellogg supplied 24 matched replicate pairs across two maize-after-soybean and four maize-after-wheat site-years. The original geographic ridge scored 1.042 MAE and 1.223 RMSE at that same site. The new candidate's calibration interval has a 4.63 t/ha half-width. One newly assessed site falls short of the release requirement, and the candidate's performance across cohorts is mixed. The production rotation comparison continues to use Water. The 15-coordinate Daymet snapshot contains about 4.5 MB of source data and the batched soil aggregate response is 620 bytes. Cached training took about 95 seconds in the verified run. All feature fetching and estimator fitting occur offline.
