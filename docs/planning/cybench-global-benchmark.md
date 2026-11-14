# CY-Bench long-lead yield benchmark

This is an executed, retrospective administrative-yield diagnostic on France and the United States. It compares fixed mean, trend, Ridge and histogram gradient boosting baselines for maize and wheat. It does not release an estimator, establish global field accuracy, or replace the crop/water runtime. All errors below come from real public CY-Bench labels and weather, not synthetic evaluation data.

## Source and reuse scope

[CY-Bench v1.10](https://zenodo.org/records/17279151) supplies the selected ZIP members. Its archive is 6,228,749,411 bytes with declared MD5 `8ec510982f396469eca25a4f0fa8632e`. The full archive was not downloaded, so that MD5 is **not verified**. Bounded HTTP Range requests retrieved complete selected members; each was deflated completely, checked against ZIP CRC32 and recorded with SHA256, byte range and compressed SHA256. The committed [report](../../packages/field-shift-ml/reports/cybench-global-v1.json) records each fitted panel’s source-member identities and feature/prediction hashes.

The Zenodo EUPL-1.2 record does not replace contributing source terms. The rights filter was frozen before any model fitting or error inspection. No China or Brazil labels entered a fit.

| Panel | Source labels (all archive years) | Complete 2008–2020 feature rows inventoried | Fitting status |
| --- | ---: | ---: | --- |
| BR maize | 230,602 | 58,233 | Excluded: `unverified_terms` |
| CN maize | 998 | 396 | Excluded: `restricted_terms` |
| FR maize | 3,026 | 1,207 | Included: JRC CC-BY-4.0 |
| US maize | 61,703 | 19,811 | Included: public USDA NASS; upstream CC0 declaration |
| CN wheat | 821 | 325 | Excluded: `restricted_terms` |
| FR wheat | 2,571 | 1,209 | Included: JRC CC-BY-4.0 |

Inventory preparation preceded the source-rights restriction; excluded panels remain inventory only. Their prepared data and raw labels are not committed or used for training. Brazil and US wheat were inventoried at source-label level but their full weather was not acquired; they are outside this six-panel acquisition and this experiment.

- France labels: Ronchetti et al. (2023), [Harmonized European Union subnational crop statistics](https://data.jrc.ec.europa.eu/dataset/685949ff-56de-4646-a8df-844b5bb5f835), DOI 10.2905/685949ff-56de-4646-a8df-844b5bb5f835, CC-BY-4.0. The harmonized NUTS2016 administrative scale is not a field measurement.
- US labels: [USDA NASS Quick Stats](https://quickstats.nass.usda.gov/); the CY-Bench source card declares public/CC0. Upstream converted original crop-specific units to metric tonnes per hectare.
- China: the [source publisher terms](https://data.stats.gov.cn/english/staticreq.htm?m=aboutctryinfo), as recorded by the upstream source card, restrict use to noncommercial purposes. This company-product experiment excludes all China fitting.
- Brazil: no sufficiently clear dataset-specific reuse grant was verified from [IBGE terms](https://www.ibge.gov.br/acesso-informacao/acoes-e-programas/termos-de-uso.html). `unverified_terms` is an evidence gap, not a claim that IBGE prohibits reuse. Later clarification requires a separately frozen experiment.
- Weather: Boogaard et al. (2020), [AgERA5](https://doi.org/10.24381/cds.6c68c9bb), C3S/ECMWF, upstream CC-BY declaration. Temperature is °C; precipitation and reference ET0 are mm/day; radiation is J/m²/day; vapor pressure deficit is hPa.
- Soil: Batjes (2016), [WISE30sec](https://doi.org/10.1016/j.geoderma.2016.01.034), ISRIC, CC-BY-3.0. AWC is the archive’s cm/m, bulk density kg/dm³ and drainage class an ordinal covariate; these are not Field Shift root-zone storage values.

The [upstream source cards and format documentation](https://github.com/WUR-AI/AgML-CY-Bench/tree/4c2c58b70b0405b9a26d94ac2aeaa9712f044b2c/data_preparation) were retained in the external acquisition cache. Crop-calendar and crop-mask files are not model features. Original source attribution remains required for reuse.

## Protocol frozen before fitting

- Active protocol: `cybench-global-long-lead-v2`, SHA256 `1ea37f9f8725f5c1035460260c9646360475ed0af9ba5d564ec46808491fb031`. Its full machine-readable specification is embedded in the report.
- Preserved earlier protocol SHA256: `80cafa05d20a9a118e95a037052230d8c2786ad9b3b3b2c1d6cbc0addfb3b90a`. Revision 2 only filters source rights; it was frozen before any fit and changes no numerical choice.
- Each test harvest year Y in 2018, 2019, 2020 has an issue date December 31 of Y−2. Actual sowing and harvest dates are unavailable; this is a long-lead hindcast, not an observed pre-sowing benchmark. Approximate lead within the named harvest year is 366–731 days; no per-region exact lead is asserted.
- Train separately per country/crop on labels from 2008 through Y−3. Label availability is conservatively **assumed** to be December 31 of label year+1, and must be at or before the issue date. Source publication histories are not available.
- The chronological track tests later years. The spatial/chronological track additionally excludes all historical labels of held-out parent groups from training: US states and French NUTS2 regions, with deterministic SHA256 modulo-five allocation declared in the protocol.
- Features: harvest year, latitude, longitude, three soil covariates, 90 recent days ending 14 days before issue, and five complete calendar years ending Y−3. Precipitation, ET0 and radiation use sums (annual means of sums for climatology); temperature and VPD use means. No target-year area, production, in-season weather, yield-derived feature, or SOS enters the feature matrix.
- Keep finite zero yields. Count and exclude negative/nonfinite/missing labels and incomplete/nonfinite static or weather windows. Daily weather must be unique, ordered and complete for each required window; no fitted or global imputation is used.
- Four fixed models: eligible-training-row country mean; OLS trend through annual country means; training-only standardized Ridge with alpha10; HGB with 100 iterations, 15 leaves, minimum leaf20, L2=1, seed0 and no early stopping. Clip predictions at zero. No hyperparameter search or model selection was performed.
- MAE, RMSE and bias are in t/ha. NRMSE divides RMSE by the test mean, not its range. R² is 1−SSE/SST and is undefined for an exactly constant target. No MAPE is reported because zero yields are valid.

The static maps and reanalysis are retrospectively reconstructed. An issue-date cutoff prevents future calendar-year observations from entering features, but cannot establish that these historical data products were actually available then. Three test years and one spatial split do not establish prospective accuracy, calibrated uncertainty, extreme-year skill, or transfer to another country/crop.

## Measured results

RMSE in t/ha, pooled across the three held-out years **within each panel and track**. Every candidate uses identical eligible rows in its panel/track; countries and crops are not pooled into a single leaderboard.

| Panel | Track | Test rows | Country mean | Country trend | Ridge | HGB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| FR maize | chronological | 277 | 2.106 | 2.084 | 2.332 | 1.886 |
| FR maize | spatial_chronological | 96 | 1.917 | 1.895 | 1.598 | 1.977 |
| US maize | chronological | 4,233 | 2.512 | 2.314 | 2.132 | 1.516 |
| US maize | spatial_chronological | 598 | 2.127 | 1.752 | 1.849 | 1.860 |
| FR wheat | chronological | 280 | 1.662 | 1.731 | 0.982 | 1.432 |
| FR wheat | spatial_chronological | 96 | 1.590 | 1.651 | 1.146 | 1.395 |

HGB has the lowest chronological maize RMSE in both countries, while Ridge has the lowest French wheat RMSE. With disjoint regions, French maize favors Ridge and US maize favors the simple trend. These are descriptive results on this frozen test set, not an invitation to deploy the test-set winner. Ridge can perform worse than the country baseline, as it does on chronological French maize.

The report includes every fold’s train/test counts and groups, MAE, RMSE, bias, NRMSE and R², including negative R². It records label exclusions and retained zero-yield counts. France maize retains four zero-yield test observations (one, two, one by year); no eligible US maize or French wheat test label is zero. Missing-feature exclusions over 2008–2020 are 26 French maize rows, 31 US maize static rows plus 163 US maize weather windows, and zero French wheat rows. Source-label counts and labels outside the experiment years are recorded separately.

Published CY-Bench baseline tables use different dataset versions, forecast cutoffs, features and fold aggregation. These results therefore cannot be compared numerically as a SOTA ranking. The coverage is two countries on two continents, two crops and administrative yield; it is not a validated eight-crop world model. No uncertainty intervals, independent farm yield validation, winter management validation, or mixed-rotation yield validation is supplied.

## Reproduction and operational bounds

Install the ML package non-editably from its frozen dependency lock, then run it from an unrelated directory. All data, prepared features, protocol files and per-row predictions require explicit external paths. No import-path injection or repository-relative data lookup is needed. A Linux example follows (`CYBENCH_PYTHON` is the installed environment’s absolute Python path):

```bash
export CYBENCH_PYTHON=/absolute/path/to/installed/ml/environment/bin/python
export CYBENCH_CACHE=/absolute/path/on/disk/cybench
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2
ulimit -v 2097152
cd /tmp
"$CYBENCH_PYTHON" -I -m field_shift_ml.cybench_global freeze --protocol "$CYBENCH_CACHE/protocol-v2.json"
"$CYBENCH_PYTHON" -I -m field_shift_ml.cybench_global acquire --protocol "$CYBENCH_CACHE/protocol-v2.json" --data-root "$CYBENCH_CACHE/selected/cybench-data"
"$CYBENCH_PYTHON" -I -m field_shift_ml.cybench_global prepare --protocol "$CYBENCH_CACHE/protocol-v2.json" --data-root "$CYBENCH_CACHE/selected/cybench-data" --prepared "$CYBENCH_CACHE/prepared-v2"
"$CYBENCH_PYTHON" -I -m field_shift_ml.cybench_global evaluate --protocol "$CYBENCH_CACHE/protocol-v2.json" --prepared "$CYBENCH_CACHE/prepared-v2" --predictions "$CYBENCH_CACHE/predictions-v2" --output "$CYBENCH_CACHE/report-v2.json"
```

Acquisition defaults to the three fit-eligible panels. It uses at most two connections, a 128 KiB ZIP64 central-directory tail, an exact four-file allowlist per selected panel, a 3 GiB compressed request budget, and an 8 GiB declared expansion budget. It rejects servers that ignore Range and checks full-member CRC/SHA. Cached files are reverified; failed downloads leave a `.partial` file and retry from the member start. There are no automatic retries. Preparation is streamed; evaluation limits native thread pools to two. The example applies a 2 GiB process virtual-memory bound. This is a local command, not a queued worker task.

The research acquisition also downloaded the three subsequently excluded panels before rights filtering, within the approved 3 GiB compressed budget. Raw data remain outside the delivered source tree; no source labels, per-row predictions, or fitted estimator are committed.

The actual 72 fits completed in 2.13 seconds, with 186.2 MiB reported peak resident memory for the evaluation process. This excludes acquisition and feature preparation. Python 3.12.16, NumPy 1.26.4, scikit-learn 1.5.2; implementation SHA256 `047b6f8f2506de19884b9ff21e3b82935fbfb247eb7775aa8004b8819ea74292`. The source hash also appears in each feature metadata record.

Validation performed: all 79 ML tests passed (52 existing and 27 new), affected Ruff checks and formatting passed, and real-data prepare/evaluate ran with `python -I` from `/tmp` against a non-editable installed wheel. Tests exercise cutoff leakage, disjoint groups, zero labels, constant-target R², missing weather, duplicate dates, checksum failures, Range enforcement and rights exclusions. Independent review and integration checks are separate acceptance steps.
