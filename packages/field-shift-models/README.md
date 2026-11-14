# Field Shift crop models

Isolated Python 3.12 crop-engine environment for planning-v1. The public API
imports only the frozen DTOs; it must not install this environment's numerical or
training dependencies. `AquaCropProvider` implements `CropModelProvider` through
injected, snapshot-specific hydraulic parameters. No provider calls, training,
model downloads, artifact promotion or HTTP routes occur on import.

From this directory, using uv and an available Python 3.12 interpreter:

```bash
uv sync --locked --python 3.12
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python -m pytest -q
.venv/bin/python -m field_shift_models.cli benchmark --check reports/cpu-v5.json
```

`benchmark --output /tmp/crop-cpu.json` measures CPU/wall time and peak RSS in
bounded child processes. It runs AquaCrop-OSPy and PCSE/WOFOST on **synthetic**
Maize/Barley scenarios and a named existing FAO-56 bucket reference. It is an
engineering decision benchmark, not an accuracy ranking: engine crop/soil
parameterizations differ, PCSE uses the bundled 2000 demo parameter calendar,
and the reference bucket explicitly uses the existing illustrative Maize Kc.
No IFS observations or field yields were acquired. Its output hashes and numeric
results must reproduce; variable timing/RSS are excluded only from `--check`.

`ingest observations.jsonl` validates canonical `TrainingObservation` records.
`train manifest.json` accepts the offline manifest described in
[model-data-requirements](../../docs/planning/model-data-requirements.md).
An empty/insufficient real dataset yields `insufficient_training_data`, null
metrics, and no changed serving artifact. Artificial labels used by unit tests
are not written into empirical reports.

Private process request: `<private-python> -m
field_shift_models.cli evaluate`, with one JSON object on stdin:

- `profile`: `FieldProfileSnapshot`; `candidate`: `CandidateSequence`;
  `weather`: `WeatherScenarioSet` from frozen planning-v1;
- `hydraulics`: explicit `ksat_mm_day` and `penetrability_percent` arrays in the
  same order as contiguous soil horizons, `source`, boolean `synthetic`, explicit
  `uniform_initial_profile: true`, optional `irrigation_events` pairs
  `["YYYY-MM-DD", net_mm]`;
- optional `footprint`: `[west, south, east, north]` for a generic baseline gate.

The response is `{result: {evaluation: SequenceEvaluation, model_card: ModelCard},
resources: {...}}`. Exact crop identity is required: crop species Maize, Wheat, Barley, Soy or Sunflower, `cultivar_id=ospy-generic`, `profile_version=3.1.0`. This is a generic
physical baseline, never a locally validated cultivar. Dry yield is t/ha at zero
moisture, reserve is plant-available mm over modeled root depth, and surface VWC
is m³/m³ over the first explicitly modeled compartment. Root/surface metrics are
at harvest; content-addressed closing-state IDs include the gap to next sowing.
Harvest dates and timestamp conventions are retained in evidence reasons.

The trusted host must authenticate and resolve immutable snapshots before
calling the process. Farm IDs and model cards supplied by clients confer no
access. Persist the exact request/result and their hashes in the owning API
module; SQL jobs, Redis dispatch, ownership and HTTP wiring belong to #7.

The parent enforces 16 MiB input/output, one child, 30 CPU seconds, 60 wall
seconds, 2 GiB address space, no core dump and one BLAS/OpenMP thread. Inference
allows at most 8 trajectories × 1100 daily records, 3 seasons and 100 soil
compartments. These are ceilings; a timeout is unavailable/error, never a zero
yield. Exit failure or timeout must not be converted into success by a caller.
Direct Python provider calls are intended for tests/offline use; production
must use the bounded launcher and OS/network isolation configured by #7.
No network sandbox or durable queue is provided by this package.

Inference is read-only and deterministic for the same canonical input and locked
code. Retry once only for a transient process/service failure, using the exact
snapshot; validation/unsupported responses are final until inputs change. No
cross-request state or partial results survive a killed child, so retry cannot
apply management or duplicate a database result. A hosting worker must fence its
own result write. Training is a separate, explicitly requested offline operation;
never retry it automatically or publish its candidate as a serving model.

Known scope: same-year, exact annual sow/harvest dates; repeated identical
crop/calendar for multiple seasons, with actual off-season daily conservation.
Mixed rotations, winter/snow/frozen soils, paddy, salinity/fertility response,
inundation, residue/tillage effects and unvalidated hybrids abstain. Irrigation
requires an explicit net uniform-sprinkler schedule within the contract ceiling;
its ceiling is never automatically applied. Missing soil, opening water,
forcing or hydraulic parameters stays missing. No calibrated predictive yield
interval exists without independent field/year calibration data.


Global research extension: see [model comparison](../../docs/planning/global-model-selection.md)
and [season/water experiments](../../docs/planning/global-water-experiments.md).
Soy maps exactly to the upstream Soybean coefficients; Sunflower uses its own
pinned profile. These are generic baseline additions, not locally validated hybrids.
The separate `ruslan` extra is experimental historical moisture reconstruction;
default crop-model serving does not load it. Development tests require LightGBM
through the locked dev group; production default installation remains unchanged.
