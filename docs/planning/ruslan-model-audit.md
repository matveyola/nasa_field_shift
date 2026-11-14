# Ruslan historical surface challenger

`field_shift_models.ruslan.HistoricalSurfaceAdapter` provides a bounded, offline
research interface to Ruslan Koritich's actual trained LightGBM surface-moisture
model. It loads the original three text models; it does not imitate their output
with a placeholder. This is a delayed historical reconstruction of surface
moisture, not a current observation, pre-sowing forecast, root-zone water reserve
or crop-yield prediction. It is not connected to the serving registry and never
promotes an artifact.

## Source and evidence boundary

The private team source is `nasa_preparation`, branch
`research/fieldshift-tasks-1-4`, commit
`468a03d79e596e9daab4cfdb20e9e0c8e2b83a92`. Artifacts come from
`research/fieldshift_tasks/outputs/real/task1_model/`. Their bytes and original
metadata are unchanged; the [provenance receipt](../../packages/field-shift-models/reports/ruslan-provenance-v1.json)
pins individual SHA256 checksums, source hashes, source-revision availability and
known evidence limitations. The packaged receipt is itself pinned by code before
bounded reads of any model file. The combined gbm/q10/q90 SHA256 is
`024c35a39470c964c6863af8d73e624ee869d211e5a10f6b954d5d2122dbe914`.

The source report records station 2025 RMSE 0.1012→0.0978 m³/m³ and IFC farm 2025
RMSE 0.0740→0.0711 versus raw ERA5-Land. These are **source-reported research
results, not independently reproduced results for this adapter**. The final
artifact was fitted on 53 stations / 44,705 station-days across 2021–2025; it is not
the individual held-out fit used for every historical score. Dry station periods
selected by the ERA5 anomaly criterion deteriorated 0.094→0.106. Broad intervals
and good aggregate metrics do not establish safety for all conditions.

Five source files differ from the source's earlier `FROZEN.json`. That frozen
manifest records the older raw-ERA5/no-publication decision; later metadata records
an L3 publication decision. The old `fresh_tests.json` therefore does not certify
this exact source revision. Raw measurement data was absent from the supplied
checkout, so empirical reproduction is blocked. The original `publish:true`
remains visible in unmodified source metadata, but this adapter ignores it and
returns `promoted=False` with explicit experimental validation status.

The [packaged NOTICE](../../packages/field-shift-models/src/field_shift_models/ruslan/NOTICE.md)
identifies the original author and adapted transforms. No public redistribution
license was found in the private team checkout; the adapter does not invent one.

## Installed interface and limits

Install the optional `ruslan` extra with the model package's locked workflow:

```bash
uv sync --locked --extra ruslan --project packages/field-shift-models
```

The extra pins LightGBM 4.6.0. It is imported only when a valid L3 correction is
requested. Raw ERA5 fallback and provenance validation work without it. Missing
LightGBM produces an explicit installation error; no alternate model is silently
loaded. Immutable resources are read with `importlib.resources`, including from a
noneditable wheel outside the repository. There is no import-path injection,
network access, fitting, pickle loading or runtime report writing. Each artifact
is bounded to 2 MiB, provenance to 64 KiB, snapshots to 10,000 days per product and each
Booster prediction to one CPU thread.

A `HistoricalRequest` supplies an estimate date, UTC `as_of`, coordinates,
region code and immutable dated `Era5Day`, `SmapDay`, optional `SoilFeatures`
records. Every supplied source needs a SHA256 identity and UTC `available_at`.
Caller acquisition/QC remains responsible for the truth of those source claims.
The wrapper validates chronology; it cannot authenticate a fabricated timestamp.
Inputs after the estimate date, unavailable at `as_of`, duplicated days or
physically invalid/nonfinite values are rejected. Daily ERA5 must not claim
availability before the complete day. This pinned final model cannot be used
with `as_of` before the source-revision receipt 2026-11-14T11:32:03Z.

Execution is deliberately limited to `US-IA` and the conservative Iowa rectangle
west/south/east/north `[-96.7,40.3,-90.1,43.6]`, April–October, soil temperature at
least 2°C, snow depth below 0.01 m. The rectangle corresponds to the IFC research
region; **it is an execution restriction, not an empirically validated footprint,
a surveyed farm or an exact state boundary**. Kyiv, Rio, equatorial, Antarctic,
arid-Asian, paddy and high-altitude examples return `out_of_domain`; they do not
receive a US-trained estimate. Nothing here declares those places unsuitable for
agriculture or extends this model's support worldwide.

The transform preserves the original 17-feature order and anomaly arithmetic:
ERA5-Land 0–7 cm surface and 7–28 cm subsurface layers, recent 3/7-day surface statistics,
past-season product climatologies, SMAP L3 anomaly and retrieval age, season,
temperature, and SSURGO sand/clay/organic matter at 5 cm. The target remains 0–5 cm
research surface moisture. Output `target_depth_cm` identifies the returned
quantity: `(0, 5)` for the learned correction and `(0, 7)` for raw ERA5 fallback.
The fallback is not a conversion to the model's 0–5 cm target. Current
year values never enter climatology. At least 30 finite past-season observations
and positive variance are required per used product. Unavailable L4 features stay
NaN: the pinned final training report had zero matching L4 rows. No L4 capability
or root-zone output is inferred.

SMAP requires an explicit nonnegative retrieval quality flag with bit 0 clear and
a finite value in (0, 0.8]. Input should already represent the upstream AM-first,
PM-fallback daily retrieval. The newest accepted retrieval must be 2–5 days old,
matching the source code's four-day search beginning at age 2. This is deliberately
spelled out because the source metadata's prose says “within 3 days”. Missing or
unacceptable L3 returns the raw ERA5 reference and no learned interval. Genuine
zero ERA5 moisture is retained. Missing recent ERA5 or required climatology/soil
is not replaced by zeros or inferred soil. The stricter wrapper also requires
explicit snow evidence and rejects incomplete trained inputs that upstream could
route through missing-value branches.

Output carries `estimated_on`, conservative snapshot `available_at` (maximum
availability of all supplied records and the source-revision receipt), source
hashes, original artifact identity, units/depth, mode, reason and non-promotion
status. It does not claim the field-resolution precision of a 9–11 km grid.
The source quantile models and conformal half-width are preserved as
`research_interval_m3_m3`, without calibrated-coverage claims. These research
bounds can extend outside physical moisture limits; they are neither clipped to
create apparent calibration nor exposed as an application confidence guarantee.
No-L3 fallback omits these research bounds entirely. Sensor-offset modes and
training/data-acquisition routines were not imported.

## Verification at this checkpoint

- Full model package: **100 passed**, no skips; 17 existing upstream deprecation
  warnings retained. This includes 44 new technical acceptance tests and 56 existing
  tests. Ruff check and format pass on the owned Python files.
- Installed original upstream code on an isolated test environment and compared
  `real.build` → `robust.RobustData` → all three original Booster outputs against
  this wrapper on deterministic inputs. Maximum absolute difference was 0.0;
  the point was 0.23630367255242443 m³/m³. A checked-in golden guards feature order,
  normalization and unrounded output. **This is synthetic numerical identity,
  not a measured accuracy evaluation.**
- Original upstream `test_past_only_climatology_excludes_later_years` passed;
  46 unrelated upstream tests were deselected explicitly. No upstream training,
  downloader or empirical benchmark was run. Original source was copied unchanged
  to an external temporary package for installation; the test-only build metadata
  is not a delivered dependency.
- Built model/contracts/science wheels, installed fresh under the model lock's
  dependency constraints with the optional extra, then executed actual inference
  from `/tmp` with `python -I`, no `PYTHONPATH`/`PYTHONHOME`. Packaged assets and
  NOTICE loaded from site-packages; `uv pip check` passed.

Logs and reproduction scripts: `/tmp/global-ruslan-tests/`. Full application,
seven-package wheel acceptance and combined integration checks belong to the
parent's final frozen-tree verification. No accuracy gate passed here, no live
provider calls occurred, and no model was published or promoted.

At the original leaf checkpoint the repository packaging guard reported two inherited path overrides in
`packages/field-shift-ml/tests/test_accuracy_benchmark.py` and
`test_audit_public_crop_data.py`, introduced by the prerequisite accuracy branch.
They are outside this lane's ownership and were reported to the parent for repair;
that leaf checkpoint did not claim a repository-wide pass. The parent subsequently removed both overrides in 9ab2f8 and verified the installed artifact path correction in 8374ee9; the combined guard and clean-wheel suites pass at d07f5ca.

### Depth metadata correction

Independent review reproduced a raw ERA5 fallback carrying the model's 0–5 cm
default depth, although its unchanged value represents the ERA5 0–7 cm layer.
The fallback now explicitly reports `(0, 7)`. Regression assertions cover the
nonzero and genuine-zero fallback paths; the real learned-model test retains
`(0, 5)`. Numerical predictions, artifacts and original research evidence are
unchanged. This corrects output metadata and does not add empirical validation.
