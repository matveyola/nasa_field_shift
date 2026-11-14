# US maize parameter search with later-year evaluation

This experiment searches model parameters using rolling validation years 2014–2017, then evaluates the frozen choices on US maize harvest years 2021–2023. It is a retrospective administrative-yield diagnostic using real public CY-Bench data. It does not publish a serving estimator or establish farm, global, or prospective forecast accuracy.

The previous [cross-country benchmark](cybench-global-benchmark.md), its source module and its report remain unchanged. Its 2018–2020 errors were already inspected, so none of those years contributes a parameter-selection score here. Their labels can enter a later final refit only when the declared availability rule permits them.

## Frozen protocol and data scope

The complete protocol is embedded in [the search report](../../packages/field-shift-ml/reports/cybench-search-v1.json). Protocol SHA256: `966f2b75db8b054f4f37753fbe60e7c6173a4a1d4fc694988a1e60efae014b27`. It was saved before fitting this real-data search. Candidate order, grids, splits, score, failure policy and resource limits were declared before any new test errors were inspected.

The source is [CY-Bench dataset v1.10](https://zenodo.org/records/17279151), using the same complete US maize yield, soil, location and meteorological ZIP members as the previous benchmark. Every source is rechecked against its recorded SHA256, CRC32 and size; source ranges and compressed hashes are included in the report. The complete archive MD5 remains unverified because the acquisition used selected complete members, not the full archive.

USDA NASS is the yield source, with public/CC0 status declared by the upstream card. AgERA5 weather and ISRIC WISE soil require their documented attribution. The Zenodo EUPL record does not supersede individual data-source terms; see the [previous source inventory](cybench-global-benchmark.md#source-and-reuse-scope) for citations and unit definitions. This experiment uses no China or Brazil labels and does not acquire additional datasets.

| Outer harvest year | Finite nonnegative source labels | Complete eligible observations | Missing static values/regions | Incomplete weather windows |
| --- | ---: | ---: | ---: | ---: |
| 2021 | 1,502 | 1,445 | 31 | 26 |
| 2022 | 1,543 | 1,485 | 31 | 27 |
| 2023 | 1,461 | 1,408 | 31 | 22 |

The chronological track has 4,338 eligible observations. The spatial outer track has 203, 211 and 212 observations by year (626 total), from states US-01, US-10, US-22, US-24, US-27 and US-29. These are administrative region-years, not independent farms. No eligible outer label is zero; finite zeros in historical training are retained. The report records exclusions and zero counts for every year, plus the groups included in every fold. The raw public labels and coverage counts had already been inventoried. Only the new prediction errors were held until selection was frozen; this is not an externally sealed benchmark.

France maize and wheat end in 2020 in the cached release. There are no French 2021–2023 labels here, and this search makes no new French accuracy claim.

## Selection and evaluation boundaries

For harvest year Y, the notional issue date is December 31 of Y−2. Label availability is conservatively assumed to be December 31 of the label's harvest year+1. Training therefore uses 2008 through Y−3, inclusive. Actual publication histories and actual sowing/harvest dates are unavailable; this is a long-lead hindcast rather than an issued or observed pre-sowing forecast.

The feature definitions match the earlier protocol: year, latitude, longitude, three static soil covariates, 90 days of weather ending 14 days before issue, and five complete calendar years ending Y−3. No target-year weather, harvested area, production, crop-calendar SOS, yield-derived input or global imputation is used. Reconstructed reanalysis and static maps can differ from products available at the historical issue date; calendar cutoffs do not remove that limitation.

Inner selection uses exactly four rolling folds:

| Validation harvest year | Training label years |
| --- | --- |
| 2014 | 2008–2011 |
| 2015 | 2008–2012 |
| 2016 | 2008–2013 |
| 2017 | 2008–2014 |

Each candidate is scored by the unweighted mean of the four validation-year RMSE values in t/ha. Exact ties use declared candidate order. All candidates must complete all folds with finite predictions; a candidate exception, invalid output, insufficient fold, timeout or memory failure aborts the experiment. A failed search never selects from the subset that happened to finish.

The chronological track uses all eligible training regions. The spatial/chronological track keeps the previous deterministic state holdout: `int(SHA256('cybench-global-v1:US:'+state),16) % 5 == 0`. Its held-out states are absent from every inner training and validation fold and from every final refit. Its outer evaluation uses only those states. Inner validation in this track still shares development states across years; the outer state holdout measures transfer that inner selection does not directly optimize.

The same four inner folds are valid before each outer issue date, so selection is computed once per track and reused. This is nested rolling validation within the outer training period, not three redundant parameter searches. Final training and test labels are:

| Outer test year | Final refit label years | Assumed issue date |
| --- | --- | --- |
| 2021 | 2008–2018 | 2019-12-31 |
| 2022 | 2008–2019 | 2020-12-31 |
| 2023 | 2008–2020 | 2021-12-31 |

No outer label is fed into a later refit: even the 2023 issue date precedes the assumed availability of the 2021 label. No 2018–2023 error enters parameter selection. Test outcomes are not used to change features, candidates, hyperparameters, or the selected identity.

## Fixed candidate grid

All predictions are clipped at zero; random seeds are zero. Ridge standardization is fit on each training fold only. The scikit-learn version remains 1.5.2 from the existing ML lock.

| Family | Declared candidates | Fixed settings |
| --- | --- | --- |
| Country mean | 1 | Mean of eligible training region-year yields |
| Country trend | 1 | OLS through unweighted annual country means |
| Ridge | 4 | Alpha 0.1, 1, 10, 100; training-only StandardScaler |
| Histogram gradient boosting | 8 | Leaves 7/15 × learning rate 0.05/0.1 × L2 1/10; 100 iterations; minimum leaf 20; no early stopping |
| Random forest | 4 | Minimum leaf 5/20 × feature fraction 0.7/1; 200 trees; depth 16; bootstrap enabled |
| Extra Trees | 4 | Same leaf/feature grid, 200 trees and depth 16; bootstrap disabled |

This is 22 candidates × 4 inner folds × 2 tracks = 176 fits. The best candidate in each of the four tuned families and the two baselines are refit for each outer year and track: 6 × 3 × 2 = 36 further fits. The overall selected identity is determined solely by the inner score, including the simple baselines as candidates.

The entire inner score table and selected identities are persisted with an immutable canonical SHA256 before outer evaluation begins. The evaluator requires that saved selection and verifies its protocol, implementation, feature hash, complete candidate set and score-derived winners. The report retains every inner candidate/fold score and all six family/baseline outer results. A lower outer error from another family is descriptive and cannot retroactively change the recorded selection.

## Measured outcomes

The immutable selection SHA256 is `d92be285d068f5d1bbee6ccc4e0c0aa99028f7285da4e5cc89246dfa59357585`. All 176 inner fits completed before it was persisted, at 220.602 seconds. The subsequent 36 final fits completed without a failed candidate, skipped fold or resource-limit event. Reported outer errors were inspected only after this complete run.

The table reports each inner-selected family configuration, not whichever configuration would look best after inspecting test errors. Inner score is mean yearly RMSE; outer RMSE/MAE/R² pool observations within a track. These are distinct aggregations.

| Track | Candidate | Preselected overall | Inner RMSE | Outer RMSE | Outer MAE | Outer R² |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| chronological | `country_mean` | No | 2.636 | 2.702 | 2.273 | -0.201 |
| chronological | `country_trend` | No | 2.993 | 2.535 | 1.936 | -0.057 |
| chronological | `ridge_a0.1` | No | 2.751 | 2.360 | 1.730 | 0.084 |
| chronological | `hgb_l15_r0.1_p1` | No | 2.222 | 1.718 | 1.397 | 0.515 |
| chronological | `random_forest_l5_f0.7` | No | 2.218 | 1.708 | 1.388 | 0.520 |
| chronological | `extra_trees_l5_f1` | Yes | 2.212 | 1.711 | 1.381 | 0.519 |
| spatial_chronological | `country_mean` | No | 2.642 | 2.213 | 1.849 | -0.257 |
| spatial_chronological | `country_trend` | No | 2.932 | 2.087 | 1.628 | -0.118 |
| spatial_chronological | `ridge_a0.1` | No | 2.688 | 1.882 | 1.460 | 0.090 |
| spatial_chronological | `hgb_l15_r0.1_p10` | Yes | 2.145 | 2.010 | 1.658 | -0.037 |
| spatial_chronological | `random_forest_l5_f0.7` | No | 2.149 | 1.968 | 1.616 | 0.005 |
| spatial_chronological | `extra_trees_l5_f1` | No | 2.152 | 1.842 | 1.497 | 0.129 |

The chronological selection, Extra Trees, attains RMSE 1.711 t/ha and R² 0.519 on 4,338 observations, compared with trend RMSE 2.535. Random forest has a slightly smaller outer RMSE, but was not the preselected overall model; that result does not change the selection.

The spatial selection, HGB, attains RMSE 2.010 t/ha and **negative R² (−0.037)** on 626 observations. Extra Trees and Ridge have smaller outer errors, but changing the winner to either after inspection would consume the holdout for selection. The recorded HGB choice remains unchanged. This exposes a limitation of choosing spatial-transfer models using validation years in already-seen development states; it is not evidence for deploying one universally best model.

All per-year scores, train/test state groups, bias and NRMSE are retained in the report, including negative R². This experiment supports a chronological US-maize diagnostic with measurable baseline improvement and a substantially weaker geographic-transfer result. No significance interval or calibrated forecast interval was estimated, and the observations are clustered by state and year.

The full fit/evaluation phase took 308.682 seconds (309.213 seconds for the external command), below the 1,200-second cap. Peak resident memory was 300.9 MiB; the separate 2 GiB address-space limit was enforced without a limit event. These timing and memory figures exclude the earlier feature-preparation process.

## Why this is not a published SOTA comparison

The [final CY-Bench data paper](https://essd.copernicus.org/articles/18/3997/2026/essd-18-3997-2026.html) describes the repository results as illustrative and does not establish an accuracy ceiling. Its target is in-season forecasting and its evaluation uses leave-one-year-out (LOYO). That differs from this experiment's long lead, strictly earlier training labels, fixed development period and later-year/state holdouts.

The [March 2025 baseline table](https://github.com/WUR-AI/AgML-CY-Bench/blob/4c2c58b70b0405b9a26d94ac2aeaa9712f044b2c/results_baselines/tables/tables_march2025_v1_8.md) explicitly uses dataset **1.8** and reports the **median of yearly LOYO fold metrics**. Its matching [dataset record](https://zenodo.org/records/15008945) declares archive MD5 `60e3c718344e5a6c09f4ed3b9ca2b2aa`. Our dataset is 1.10; weather sources and columns changed in 1.9. Our report pools outer observations within a track and also gives each year's metrics, rather than silently equating those with the published median.

A valid baseline reproduction would need the exact historical code/configuration, dataset-1.8 members and eligible rows, mid-season crop-calendar cutoffs, full in-season weather/fPAR/NDVI/soil-moisture/static inputs, all-other-years LOYO training, nested tuning settings and the same fold aggregation. The table does not embed an exact execution revision or dependency environment. The current [code release v1.0.0](https://github.com/WUR-AI/AgML-CY-Bench/tree/4c2c58b70b0405b9a26d94ac2aeaa9712f044b2c), also archived as [Zenodo 20456376](https://zenodo.org/records/20456376), is software, not a new dataset. Its loader expects newer location features and is not automatically an exact reproduction of the 1.8 table.

A CPU-only mean/trend/Ridge/RF reproduction on a bounded country subset is plausible, but the stock pipeline loads large CSVs into pandas and imports neural-network dependencies; the full upstream benchmark's resource estimates describe a much larger GPU run. No such reproduction was performed here. Comparing our errors to those published numbers as a leaderboard would compare different tasks. This parameter search improves the evidence for a limited model-selection procedure; it does not establish SOTA, global crop coverage, uncertainty calibration, mixed-rotation yield validity or farm-level accuracy.

## Reproduction and failure handling

Use the installed ML package and existing verified US member cache. No network acquisition is performed by this module. Linux commands from an unrelated directory:

```bash
export CYBENCH_SEARCH_PYTHON=/absolute/path/to/installed/ml/environment/bin/python
export CYBENCH_SEARCH_CACHE=/absolute/path/on/disk/cybench-search-v1
export CYBENCH_DATA=/absolute/path/on/disk/cybench/selected/cybench-data
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2
cd /tmp
"$CYBENCH_SEARCH_PYTHON" -I -m field_shift_ml.cybench_search freeze --protocol "$CYBENCH_SEARCH_CACHE/protocol.json"
"$CYBENCH_SEARCH_PYTHON" -I -m field_shift_ml.cybench_search prepare --protocol "$CYBENCH_SEARCH_CACHE/protocol.json" --data-root "$CYBENCH_DATA" --prepared "$CYBENCH_SEARCH_CACHE/prepared.csv"
timeout --signal=TERM --kill-after=5s 1200s "$CYBENCH_SEARCH_PYTHON" -I -m field_shift_ml.cybench_search run --protocol "$CYBENCH_SEARCH_CACHE/protocol.json" --prepared "$CYBENCH_SEARCH_CACHE/prepared.csv" --selection "$CYBENCH_SEARCH_CACHE/selection.json" --predictions "$CYBENCH_SEARCH_CACHE/predictions.csv" --output "$CYBENCH_SEARCH_CACHE/report.json"
```

The module enforces a 2 GiB address-space limit and a 20-minute wall alarm; the command also supplies an external 20-minute timeout. Native and forest parallelism are limited to two threads in one process. All 212 fits run serially at the candidate/fold level. Preparation streams the daily source file and has its own equivalent process bound. Neither raw data nor per-observation predictions nor fitted estimators are committed.

There is no automatic retry and no queued production worker. Existing protocol/selection files cannot be replaced with different contents. Existing result/prediction paths are rejected so a failed replay cannot masquerade as a prior successful output. A failed outer evaluation can leave partial predictions and an already valid inner selection; its nonzero exit and absent completed report mean the experiment is incomplete. Replays use new result/prediction paths and preserve earlier evidence. Rebuilding the installed wheel is required after source changes, since prepared metadata binds the exact module bytes.


## Verification and provenance

The non-editable installed ML wheel was executed under `python -I` from `/tmp`. All 193 ML tests pass, including 22 new tests covering every estimator family, rolling cutoffs, exclusion of opened/test outcomes from selection, state separation, zero/missing labels, incomplete weather, immutable selection, tampering, failed candidates and timeout behavior. Affected Ruff and formatting checks and the Python packaging guard pass. No test or expectation was weakened. Independent verification and final integration are separate acceptance steps.

Search module SHA256: `36b45d1f139ecfe72a62de0908a294f0871e9b8896f117278e8939bcbab03476`. Preserved baseline module SHA256: `047b6f8f2506de19884b9ff21e3b82935fbfb247eb7775aa8004b8819ea74292`. Prepared data SHA256: `d48b636de478c4530f3199dac4bb21cd0a386d271660ccfa6965065d784c67aa`. Per-observation prediction SHA256: `93de1c210983dc5cb77d2cfe7385a76624d018d19b392ace1a239071c61d6336`. The report embeds these identities and each raw member identity.

No source in the earlier benchmark, dependency manifest/lock, public API contract or serving path changed. Historical reports remain intact. Raw inputs, prepared data, frozen protocol/selection files, per-observation predictions and execution logs remain in the explicit external research cache.
