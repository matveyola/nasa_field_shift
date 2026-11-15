# Global model evaluation and applicability

The current result is a reproducible set of open-data experiments and bounded model adapters. It is not independently validated field-yield coverage for the whole world. There is no single defensible winner across targets, crops, regions and lead times.

## What can execute

| Component | Supported execution | Scientific status |
| --- | --- | --- |
| Native AquaCrop-OSPy 3.1 | Maize, Wheat, Barley, Soy (exact Soybean profile), Sunflower; current same-year annual management contract | Generic baseline; no new region/cultivar validation. Winter/snow, mixed sequences and paddy remain outside this serving adapter. |
| Explicit layered-water experiment | Seven non-paddy crop identities with caller-supplied calendars, phases, soil layers, opening water/snow and freeze history; both hemispheres, cross-year dormant periods and mixed sequences | Experimental water accounting only; no yield, frost survival, nitrogen, inundation or profit estimate. |
| Rice | Native OSPy has PaddyRice, but this project's paddy management and validation contracts are absent | Explicit unsupported; no substitution with another crop or an upland bucket. |
| Ruslan historical moisture adapter | Original LightGBM assets and feature transform, restricted historical Iowa execution; missing SMAP uses labeled raw ERA5 fallback | Historical reconstruction, not future seasonal prediction. Original accuracy could not be independently reproduced without its raw observations. |
| Yield learners | Actual ARDN plot trials and CY-Bench administrative labels in the scopes below | Offline retrospective diagnostics, not promoted serving artifacts. |

The [process-model comparison](global-model-selection.md) covers exact AquaCrop, WOFOST, DSSAT, APSIM, STICS, MONICA, ORYZA and differentiable Agrocosm variants and their input, rights, runtime and reproducibility gaps. Representation models and atmospheric forecasts are different tasks; their published scores cannot be transferred to crop yield.

## Measured open-data results

[CY-Bench](cybench-global-benchmark.md) compares the same eligible rows per country/crop under a frozen long-lead protocol. RMSE below is t/ha, pooled within the named panel and track. These are descriptive test results, not test-set model selection or a SOTA leaderboard.

| Panel/track | Country mean | Trend | Ridge | HGB |
| --- | ---: | ---: | ---: | ---: |
| US maize, future years | 2.512 | 2.314 | 2.132 | 1.516 |
| US maize, future years and disjoint states | 2.127 | 1.752 | 1.849 | 1.860 |
| FR maize, future years | 2.106 | 2.084 | 2.332 | 1.886 |
| FR maize, future years and disjoint regions | 1.917 | 1.895 | 1.598 | 1.977 |
| FR wheat, future years | 1.662 | 1.731 | 0.982 | 1.432 |
| FR wheat, future years and disjoint regions | 1.590 | 1.651 | 1.146 | 1.395 |

The US [ARDN/NASA POWER diagnostic](global-data-sources.md) includes 3,751 calendar-valid dry-yield records, 20 conservative physical site groups and maize/soybean/wheat. At the early issue, selected maize GBDT has MAE 2.070 versus mean 3.349 t/ha, soybean 0.747 versus 0.886 (R² remains negative), and wheat Ridge 2.235 versus mean 1.199: a failure. At held-out KELLOGG maize, GBDT 3.175 versus mean 1.696 also fails. The reports retain every failure. These plot trials and the administrative benchmark have different targets/populations and must not be pooled.

Sources are public and retrieved without service credentials: NASA POWER, USDA/ARDN, and source-rights-filtered CY-Bench France/US. NASA inputs are coarse gridded weather, not field sensors. Raw caches remain outside Git with acquisition receipts and checksums. The source registry documents additional candidates; a catalog entry does not mean its data were acquired, fitted or validated.

## Fresh-year US parameter search

The separate [US search](cybench-search.md) freezes 22 candidates, four rolling 2014–2017 inner folds and 2021–2023 outer tests. A selection hash is persisted before outer errors.176 inner fits plus 36 final fits ran under the two-thread/2GiB/20-minute bound. Public labels were inventoried; no externally sealed holdout is claimed.

The preselected chronological Extra Trees model gives RMSE 1.7109 t/ha, MAE 1.3814 and R² 0.5185 on 4,338 eligible observations, versus trend RMSE 2.5354. The preselected spatial HGB gives RMSE 2.0101, MAE 1.6579 and R² −0.0372 on 626 held-out-state observations, versus trend 2.0866. That negative R² and the weak spatial transfer remain failures of the broader accuracy ambition. Extra Trees scores better on the spatial outer test, but switching to it after seeing that test would invalidate the predeclared choice; no such switch or production promotion occurred.

The published CY-Bench baseline tables use another dataset version, in-season predictors and different temporal splits/aggregation. They are illustrative reference results, not a matched SOTA score for this long-lead task. No universal or operational claim follows from this bounded search.

## Geographic and season behavior

The [six-location execution cases](global-water-experiments.md) use actual NASA weather at Kyiv, Rio, Singapore, Sahara, Antarctica and La Paz. Controlled soil/crop assumptions isolate weather and cannot rank species. Ice abstains, unconfirmed arable land remains unknown and rice remains unsupported. Passing these cases proves code behavior, not a yield error or a guarantee that a fertile field can grow every crop. Solar radiation, latitude and hemisphere affect supplied forcing/calendars, but do not replace agronomic suitability evidence.

Historical weather members retain continuous daily sequences and common source years across nearby fields. Source availability cannot precede completion of its observations or exceed issue time. Member frequencies are uncalibrated scenarios, not event probabilities; three-year planning is not a daily three-year weather prediction.

## Before a global accuracy claim

A new region/crop requires matched local soil/profile/calendar/management parameters, target-compatible observations, true issue-time forcing, and independent year/site evidence against retained baselines. Evaluate uncertainty calibration and extreme-event performance separately. No current result supplies calibrated global field-yield intervals, validated paddy/winter yield or crop-choice profit/regret ground truth. Keep failed release gates and explicit abstentions; never publish a universal accuracy percentage from these experiments.
