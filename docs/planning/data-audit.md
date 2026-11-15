# Data and release-evidence audit — 2026-11-14

The supplied project archive and existing ML package contain model artifacts,
acquisition scripts and previous evaluation reports. The inspected ZIP entries
and project/cache paths did not contain raw field-observation CSV, JSONL,
Parquet or spreadsheet datasets. This is a scoped search, not proof that no
observations exist elsewhere on the computer.

These existing reports must not be confused with new validation of the OSPy
planning baseline:

| Existing report under `packages/field-shift-ml/src/field_shift_ml/evaluations` | Recorded finding |
| --- | --- |
| `rotation-yield-iteration.json` | `research_only`; fresh farm coverage, predecessor coverage and MAE improvement gates fail; RMSE improvement and interval coverage pass |
| `iowa-fields-v2.json` | Seven sites / 1680 paired days; reported model MAE approximately 0.05681 versus ERA5 0.05672 m³/m³; multiple promotion checks fail |
| `root-zone-research.json` | `promoted: false`; research diagnostics are not permission to enable production promotion |

Report SHA-256 values, respectively:

```text
b2c228adc30994ee201e2d5d4f00a5caf24df12b76e52c1ac5c9e42f46f8cd63
36d5a752a0b0b8419ff9f6b1ff16049436559162decdb11ab7ed9c5e3ef99ad8
f263d0db954f364785022c24c1a1ba5034c11bc3d8e4107eec94349662124e09
```

The rotation report records USDA DOI `10.15482/USDA.ADC/1411953`, Morrow Plots
DOI `10.13012/B2IDB-7865141_V2` and Zhou et al. DOI `10.5281/zenodo.13901372`,
including dataset checksums. Acquisition scripts also exist for soil-moisture
observations. These are discovery leads; the raw observations, issue-time
weather, permissions and crosswalk to new immutable field snapshots still need
verification before fitting or claiming a reproducible new empirical benchmark.
This integration does not claim to have rerun those historical evaluations.

The frozen `contracts/planning-v1/empirical-sites.v1.json` has no sites and says
`insufficient_data`. The new CPU benchmark reproduces the frozen synthetic
report, with null accuracy metrics and `baseline_only` promotion. The observed
live acceptance calculations likewise use synthetic weather/soil and explicit
user yield assumptions. Neither is a trial or evidence of profit improvement.

## What the pilot needs

Choose a region and crops, then provide permissioned records with stable field
IDs/contours, soil horizons, dated sowing/harvest and irrigation, cultivar,
management and history. For yield targets supply harvest year, t/ha, moisture
basis and source; for water targets supply timestamps, sensor depth, units and
QC. Preserve when each input became available, including archived forecast
issue/cycle/lead and full forcing. Keep farms/geographic groups and years
separated across fitting, calibration and sealed holdout evaluation.

See [the canonical ingestion and evaluation protocol](model-data-requirements.md).
A spreadsheet export is sufficient as a starting point; storing it in a new
database does not add missing measurements or improve accuracy. Keep raw
permissioned data outside Git and retain checksums with the evaluation manifest.
Issue #4's independent field validation remains outstanding.

## Storage decision

Keep PostgreSQL authoritative for profiles, plans, permissions, attempts and
results. Use versioned local/object-store files for reproducible offline
training datasets. ClickHouse can later serve a separate analytical projection
of large observation/prediction histories if measured query latency and volume
justify it. It would not replace transactional job state, release gates or the
need for labelled field data. No ClickHouse deployment or data transfer is part
of this integration.
