# Accuracy evidence and model strategy

Evidence reviewed 2026-11-14. This is an offline evaluation checkpoint, not a model promotion. No new crop-engine accuracy or universal SOTA claim is established. Existing production release gates and engines are unchanged.

## What the existing evidence actually says

The reproducible [report audit](../../packages/field-shift-ml/reports/evidence-audit-v1.json) reanalyses already-opened reports. It does not rerun inference or turn previous model selection into an independent test. Lower MAE/RMSE is better. Surface moisture units are volumetric m³/m³; rotation-effect errors are t/ha and are not absolute yield errors.

| Existing diagnostic | Reference MAE → candidate MAE | Reference RMSE → candidate RMSE | Interpretation |
| --- | --- | --- | --- |
| Historical surface V2, seven qualified Iowa farms, 1,680 days | 0.056723 → 0.056808 | 0.073651 → 0.070349 | MAE 0.15% worse; RMSE 4.48% better |
| Same model, serving footprint, four farms / 1,026 days | 0.053231 → 0.057968 | 0.066903 → 0.072901 | Both errors about 9% worse |
| Legacy IFS49R1 surface correction, 1,048 pairs / eight sites | 0.082720 → 0.079613 | 0.104580 → 0.097290 | MAE 3.76% better; recorded release gates still fail |
| Existing rotation-effect model | 1.071976 → 1.188409 | 1.584109 → 1.605555 | MAE 10.86% worse; RMSE 1.35% worse |

V2 wins on only two of seven farms. Paired farm-bootstrap 95% intervals for candidate-minus-reference MAE [-0.013617, 0.009551] and RMSE [-0.019668, 0.009417] include zero. These intervals are conditional on the seven declared farms, do not establish geographic independence, and do not include selection uncertainty. The serving-footprint aggregate has no per-farm subset predictions, so no invented CI is reported. One 24-day farm is excluded by the original minimum-pair qualification, explicitly recorded in the audit.

The root-zone artifact remains unpromoted. Root-zone reserve in mm and surface moisture cannot share accuracy numbers. Old IFS-cycle results do not establish performance on current cycles, Ukrainian fields, other crops or unseen climates.

## Crops and territories: data first, sequential rollout

The application list is **Wheat, Maize, Soy, Barley, Sunflower, Rapeseed, Peas, Rice**. Dataset presence, engine parameter availability and validated deployment support are three different things.

The [machine-readable coverage matrix](../../packages/field-shift-ml/reports/crop-territory-coverage-v1.json) covers all eight crops, explicitly recording gaps. Its source is the final [CY-Bench paper](https://essd.copernicus.org/articles/18/3997/2026/essd-18-3997-2026.html), tables 2–4: maize 38 countries / 149,393 administrative labels; wheat 29 countries / 65,862 labels; union 43 countries. Other application crops have no established coverage **in this source**, not global absence. Ukraine is absent from this published maize/wheat inventory. Administrative labels cannot validate individual fields. Min/max years are envelopes, not complete annual coverage.

The paper transcription is retained as [CSV](../../packages/field-shift-ml/benchmarks/metadata/cybench-paper-coverage.csv). Latest inspected archive metadata: [Zenodo 17279151](https://zenodo.org/records/17279151), v1.10, 2025-10-06, 6,228,749,411 bytes, MD5 8ec510982f396469eca25a4f0fa8632e. The archive was not downloaded; paper metadata is not a verified archive inventory. Constituent source terms still need review.

Acquired [USDA/ARDN field data](https://doi.org/10.15482/USDA.ADC/1529186) add 3,797 plot-year grain records from 2011–2015: maize 2,300, soy 1,362, wheat 135; eight US states and 29 experiment/site codes. Site codes are not proven independent farms. [Readiness audit](../../packages/field-shift-ml/reports/public-crop-data-readiness-v1.json) cross-checks all grain yields against the original workbook: ARDN uses **dry kg/ha**, matching standard-moisture parent yields after conversion within 0.11 kg/ha. Do not correct these dry yields again. CC0 metadata includes a February 2026 warning about wrongly assigned nitrogen rates; nitrogen-response validation is blocked until corrected.

Only 290 corn calendars have complete supplied temperature/rain plus on-site radiation/wind; humidity/vapour pressure or ET0 is absent. Measured soil reaches 120 cm; initial profile water and validated cultivar mappings are missing. Zero current-wrapper-ready records is a structural assessment, not 3,797 executed engine validations. A narrower native retrospective experiment may use declared generic soil/cultivar/initial-water scenarios, but these assumptions cannot be presented as measurements. Forecast provenance and plot polygons are not prerequisites for that narrower historical experiment. Current synthetic weather constants must never substitute for missing empirical forcing.

Sequence:

1. Inventory maize, wheat and soy first, retaining every supported country and explicit per-year missingness. Acquire complete historical forcing for predeclared USDA station-years, review weather units/wind height, joins and irrigation status, then freeze a conditional native-engine comparison before looking at errors.
2. Evaluate administrative yield independently across CY-Bench countries. Compare trend/climatology, ridge and GBDT before adapting larger models; separate rolling future-year, unseen-region and combined holdouts. Do not transfer administrative scores to fields.
3. Expand barley, sunflower, rapeseed, peas and rice by authoritative regional sources with field/admin truth recorded separately. [Eurostat](https://ec.europa.eu/eurostat/cache/metadata/en/apro_cp_esms.htm) and [FAOSTAT](https://data.fao.org/catalog/iso/d24a448b-3b62-4c09-8c1d-4a39bb599876) are acquisition leads, not verified crop-country completeness. Ukrainian commercial readiness needs Ukrainian measurements and provenance.
4. Publish crop × climate × region × lead coverage and confidence, including abstentions. A missing-data or out-of-domain result is an expected outcome; do not fill every cell with an invented prediction.

## Best candidate architecture, not a proved local SOTA winner

Use pretrained weather outputs plus a process engine with measured inputs, then add small learned corrections only where independent validation supports them. Training a global foundation model from scratch is unnecessary. Parameter calibration, weather preparation and task-specific validation remain necessary; a ready engine is not a universally calibrated predictor.

- **Weather:** benchmark native IFS and [AIFS Single/ENS](https://www.ecmwf.int/en/forecasts/dataset/aifs-machine-learning-data), pinning cycle, issue time and available variables. ECMWF reports AIFS v2 operational since 2026-05-12. Prefer available native forecasts to maintaining our own global weather inference stack. Ensemble spread needs observed calibration; it is not automatically crop-yield uncertainty.
- **Crop/water baseline:** retain AquaCrop and our own FAO56 option. Challenge AquaCrop with [PCSE WOFOST](https://pcse.readthedocs.io/en/stable/available_models.html), choosing water-limited/multilayer/nitrogen variants according to the target and data. Consider [DSSAT](https://github.com/DSSAT/dssat-csm-os) for justified crop/management coverage such as rice. Verify parameter-set rights as well as engine licenses. Do not rank these engines by unrelated published scores.
- **EO:** [Prithvi-EO-2.0](https://arxiv.org/abs/2412.02732) and [TerraMind](https://arxiv.org/abs/2504.11171) are pretrained representation candidates, requiring a task head and appropriate labels; they are not zero-shot universal yield products. [PhenoYieldNet](https://arxiv.org/abs/2605.23478) uses adaptation and supervised county-yield training. [YieldSAT](https://yieldsat.github.io/) offers field-yield research data under restricted access/terms, not unrestricted commercial truth.
- **Three-year rotations:** optimize explicit agronomic/economic constraints across weather scenarios and replan annually. [ECMWF seasonal products](https://www.ecmwf.int/en/forecasts/datasets/set-v) extend months, not a reliable daily forecast for three years. Do not turn scenarios into certainty.

This hybrid is the recommended evaluation direction based on task fit, replaceability and available evidence. A local SOTA claim requires it to beat retained baselines on the same frozen inputs, targets, coverage and independent holdouts, with tolerable compute and licensing costs.

## Evaluation and product behavior

The new offline paired evaluator checks exact predeclared observation membership, pinned models and forcing, units/depth/harvest basis, issue-time availability, separate temporal/spatial groups, missing-label coverage, abstentions and per-cohort coverage. It reports equal-farm errors and paired farm-bootstrap intervals. Thresholds are caller-declared policy, not universal scientific constants. Diagnostic reuse and synthetic labels cannot establish release eligibility. Files and timestamps cannot prove sealed access; external audit evidence is still required.

No interface changed in this checkpoint. A subsequent UI integration should show engine/version, crop/region applicability, input gaps, forecast horizon, validation scope and an uncertainty range only when calibrated. Distinguish measured, forecast and scenario outputs. Selecting “ready” versus “own” through environment configuration does not itself improve accuracy or override release gates; the earlier model-selection branch is separate work. Never display one global “accuracy %” from the diagnostics above.
