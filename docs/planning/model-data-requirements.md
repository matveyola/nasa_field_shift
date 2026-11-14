# Offline model data, evaluation and release boundary

Lane B keeps offline dependencies, observations and training outside the API
image. No real farm observations or permissioned imagery were supplied for this
checkpoint. The correct initial outcome is `insufficient_training_data`, null
accuracy metrics, and the named uncalibrated OSPy baseline (or explicit abstention
when unsupported). Synthetic weather/geometry tests never count as empirical
validation, farms, years, harvest labels or promotion evidence.

## Observations and input snapshots

`field-shift-models ingest observations.jsonl` reads one canonical planning-v1
`TrainingObservation` JSON per line. It rejects malformed units, moisture basis,
missing sensor depth, bad chronology, impossible values and duplicate IDs. It
preserves zero and null separately. Records retain pseudonymous farm, field,
geographic/grid groups, geometry revision, region, crop/cultivar/profile, dates,
observed/available times, QC, source checksum/locator/permission, management and
irrigation, sow/harvest, phenology and field-snapshot references. Soil and full
management inputs belong to the permissioned immutable profile snapshot, not
invented flat training columns. Client observations stay outside Git.

Only real, valid, finite labels can contribute to fitting or empirical metrics.
A yield label must include crop and dry/standard/fresh moisture basis. Separate
surface soil moisture targets by depth and lead; root reserve in mm is never
trained as if it were VWC. Never combine cultivar/basis/lead targets in one
residual learner or label a field-average yield as pixel ground truth.

The inference host must supply soil Ksat in mm/day, penetrability in percent and
their source per horizon. These quantities have no shared planning-v1 fields;
this is a private injected parameterization, not a fork of the contract. A
uniform initial depth profile is an explicit modeling assumption. For actual
nonuniform sensor profiles, supply a separately reviewed adapter/contract
extension; do not flatten measurements silently. Dated net irrigation schedules
must be known at issue time and independently permissioned. The host's immutable
parameter-source record provides that availability/permission evidence.

## Manifest, fitting and calibration

`field-shift-models train manifest.json` accepts:

- `gate`: all `learning.Gate` fields, with UTC `frozen_at`; `gate_sha256` must equal
  the canonical checksum exposed by that Gate object;
- `holdout_opened_at`: UTC instant strictly after protocol freeze;
- `rows`: `learning.Row` records, each containing canonical `observation`, UTC
  `issue_at`, `predictor_available_at`, `cycle_at`, pinned `forecast_model`,
  `forcing_kind`, `lead_days`, WGS84 `latitude`/`longitude`, finite-or-null
  `features`, a frozen named baseline `reference`, and explicit
  `partition` (`fit`, `calibration`, `test`).

Inputs are local offline operator artifacts, not accepted from untrusted API
clients. The operator must seal the protocol and split manifests in an audit
store **before** opening test labels. A hash/timestamp in JSON alone cannot
prove historical sealing. Keep source/feature schema, reference artifact
versions and permissions alongside that manifest; its full content hash is
included in the training report. The package bounds one benchmark to 10,000
rows; larger campaigns need a separately budgeted grouped-cohort runner.

The pipeline rejects cross-partition farm, weather-grid or geographic groups,
non-future calibration/test years and locations within the caller's positive
geographic buffer. It compares UTC available time to issue time; labels may
become available afterward, predictors may not. The forecast cycle and label
lead must match. `archived_forecast` and `perfect_weather` get separate reports;
passing final reanalysis as an archived operational forecast is rejected. The
operator must verify the underlying archive/source hashes, not just its name.

Ridge regression (`alpha=10`) and shallow histogram boosting (depth 2, four leaf
nodes, 50 iterations, seed 0) learn baseline residuals. Each pipeline fits
imputation/scaling only on fit data. Calibration residuals alone define finite
sample absolute-error intervals. Test labels are used only for evaluation.
Reports retain cohort MAE/RMSE/bias by region, farm, crop, year and lead, interval
coverage/width, runtime, and actual failed gates. The two learners are reported
separately; selecting a winner repeatedly on test labels is prohibited.

`calibrate_one_parameter` searches 2–21 predeclared values for exactly one named
physical parameter (`WP` or `Ksat`) inside caller-supplied positive physical
bounds. Only fit trials may be passed. It requires at least six real observations,
three farms and two years before calling the injected ready-engine evaluator;
this floor is a guard against fitting many coefficients to one harvest, not a
claim that six observations identify every parameter. Scientific pilot design
may require more. The evaluator receives `None` for the unchanged frozen generic
comparator. OSPy `run_engine(..., crop_wp=value)` changes its existing WP parameter;
Ksat experiments replace explicit `Parameters.ksat_mm_day`. No crop physiology
is rewritten. Trial-input hash, generic comparison and candidate fit metrics are
recorded; a fit optimum remains `candidate`, never a release. The operator must
supply independent calibration/holdout observations for any promotion.

## Release protocol

Before test access, define independent farms/years/rows, calibration sample
count, minimum relative MAE improvement, maximum per-cohort MAE regression,
minimum coverage, maximum interval width, positive geographic buffer and fit
runtime ceiling. No universal accuracy thresholds are invented here. Production
thresholds require an agronomist-reviewed pilot protocol. Empty labels or
insufficient groups produce `insufficient_training_data` / `insufficient_evidence`.
Zero-width intervals, coverage failure, regression or exceeded runtime fail the
gate. Passing an artificial unit test does not pass a scientific release.

A passing offline report is evidence for a separately reviewed, versioned
adapter. Before serving it, the release owner must bind its exact candidate
artifact/parameter/license checksums, immutable data/partition manifests and
cohort metrics to canonical `ModelCard`, validated crop/region/management scope,
gate protocol and fallback. No local candidate artifact exists at this checkpoint,
so serving deliberately remains the baseline. There is no unreviewed pickle
loader or automatic promotion endpoint. Failed, insufficient or OOD local
adaptation cannot replace the named baseline or suppress an abstention. Existing
model gates/artifacts under `field-shift-ml` and API modules remain unchanged.

## Optional foundation model experiment

`experiments.compare_frozen_embeddings` compares supplied frozen Prithvi tiny or
100M features plus small supervised heads against ordinary features including
NDVI, on identical grouped future partitions and the same protocol. Missing
labels or embeddings returns `not_run` / `insufficient_training_data` with null
metrics. No checkpoint, HLS imagery or GPU budget was provided, so no extraction,
fine-tuning or measured Prithvi advantage is claimed.

The extractor manifest pins checkpoint/checksum, extractor version, blue/green/
red/narrow-NIR/SWIR1/SWIR2 order, reflectance conversion to physical [0,1], temporal
and geolocation handling, pooling and permissions. Embedding hashes and each
sample's actual availability are checked before fitting. Geolocation and date
metadata follow the selected TL model; frozen feature extraction must not leak
future acquisition/availability. Field labels are aggregate targets, explicitly
not pixel ground truth. Radiometry/cloud masks/resampling must be independently
reviewed before running the comparison.

[Prithvi EO 100M TL model card](https://huggingface.co/ibm-nasa-geospatial/Prithvi-EO-2.0-100M-TL)
describes pretrained representations, not a ready crop-yield predictor. Exact
checkpoint and dataset terms must be recorded when artifacts are acquired. Full
fine-tuning requires independent labels and an explicit compute budget. Large
[Prithvi WxC](https://huggingface.co/ibm-nasa-geospatial/Prithvi-WxC-1.0-2300M)
training remains future R&D; the MVP consumes ready weather ensembles and can
evaluate local bias/spread corrections after an issue-time archive exists.

## Integration handoff

#7 mounts authenticated transport and durable jobs. Use the package's bounded
private process protocol; never install its training dependencies in the API
image. Preserve unsupported/insufficient/error output, exact model/input hashes,
and UTC dates. Do not turn process failure into a successful yield=0. The
[package README](../../packages/field-shift-models/README.md) declares payload,
CPU/memory/time bounds, retry, idempotency and killed-process recovery.

Shared contracts, generated types, source providers, promoted artifacts,
`docs/status.md`, CI, Compose and root registries are read-only in this lane.
The evidence here and in the package reports is the status handoff for #7.
