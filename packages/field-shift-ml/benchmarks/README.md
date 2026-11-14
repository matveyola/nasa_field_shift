# Offline accuracy evidence

See [strategy and scope](../../../docs/planning/accuracy-and-model-strategy.md). All workflows are offline evaluation, separate from production promotion. Install this package with its locked environment; use `python -m field_shift_ml.<workflow>`. No new runtime dependencies or path injection are introduced here.

From the repository root, using the package environment:

```sh
packages/field-shift-ml/.venv/bin/python -m field_shift_ml.audit_model_evidence \
  --reports-dir packages/field-shift-ml/src/field_shift_ml/evaluations \
  --artifacts-dir apps/api/src/field_shift/modules \
  --output packages/field-shift-ml/reports/evidence-audit-v1.json
packages/field-shift-ml/.venv/bin/python -m field_shift_ml.data_coverage \
  --metadata packages/field-shift-ml/benchmarks/metadata/cybench-paper-coverage.csv \
  --output packages/field-shift-ml/reports/crop-territory-coverage-v1.json
packages/field-shift-ml/.venv/bin/python -m field_shift_ml.accuracy_benchmark \
  --predictions /absolute/path/predictions.jsonl \
  --manifest /absolute/path/frozen-manifest.json --output /absolute/path/report.json
```

The evaluator module docstring defines the exact schema, `GATE_FIELDS` its required policy fields; tests contain explicitly artificial schema fixtures. No example fixture is empirical evidence. Register every expected observation before predictions, including missing labels and abstentions. Freeze selection, thresholds and models before opening the holdout. Group aliases sharing a physical farm/grid together; forward temporal tests differ from leave-one-year-out tests. Separate operational forecasts, historical reconstruction and perfect-weather experiments. Root-zone mm, surface m³/m³ and dry or standard-moisture t/ha are incompatible targets.

Reports under `reports/` are small derived evidence, not deployable model artifacts. The historical audit reads existing reports/artifacts without mutating them. Its conditional report-level bootstrap is not raw-prediction re-evaluation. Missing crop-engine comparisons remain `insufficient_data`.

## Source inventory and reproduction

`metadata/cybench-paper-coverage.csv` transcribes final CY-Bench paper tables 3/4 and administrative levels from table 2, [paper](https://essd.copernicus.org/articles/18/3997/2026/essd-18-3997-2026.html), CC BY 4.0; transcription/normalization changes include canonical GR retaining source EL. No exact-year completeness is inferred. Latest archive was inspected as metadata only, not downloaded or independently inventoried.

`metadata/agrobench-state-coverage.csv` is an unmodified author metadata file from [AgroBench commit 790bca53](https://github.com/udaiveersingh/AgroBench/blob/790bca53f313216c477f5f34a16e34ab48b4b1a1/metadata/state_coverage.csv); repository MIT license, claimed data terms require authoritative dataset verification. County-to-pixel weak labels are not independent field truth. This supplemental file does not feed CY-Bench country counts.

`metadata/public-source-receipts.json` records canonical URLs, retrieval UTC, sizes, SHA256 and permission notes for the bounded public acquisition (28,977,190 bytes total including metadata). Raw inputs stay outside Git. USDA original DOI [1411953](https://doi.org/10.15482/USDA.ADC/1411953), ARDN child [1529186](https://doi.org/10.15482/USDA.ADC/1529186), CC0; retain the fertilizer-rate warning. IFC [source](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/) is CC BY 4.0; cite Marcela Rojas (2026), and identify derived aggregation. Not every acquired IFC day matches old evaluation filtering.

To reproduce the public crop audit, retrieve the named files in the receipt into a separate data directory and verify their hashes; run `python -m field_shift_ml.audit_public_crop_data --data-dir /absolute/path/data --output /absolute/path/readiness.json`. This audit does not download, fit or execute a crop engine. Full native-engine forcing acquisition and holdout sealing remain subsequent work.

This branch starts at `95806387115645d65b95221bfcd3f070c828afa3`. The commands above name that base's artifact layout. Parallel packaging work has since moved scientific artifacts into `field-shift-science`; when integrating there, supply the corresponding installed/resource-export directory to `--artifacts-dir` and rerun reproduction. The evaluator and public-data audit do not import API modules or require source-path injection. Do not restore old API paths to accommodate this documentation.
