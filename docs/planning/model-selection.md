# Crop engine decision and limits

Lane B / issue #4, frozen planning contracts
`ebefa04d9907e5c3819bd23757e032c4e565f6ff`. This checkpoint is independently
executable through the isolated package CLI; public serving and jobs wait for #7.
It does not change existing Field Shift model artifacts or their release gates.

Use AquaCrop-OSPy **3.1.0** as an explicitly generic, uncalibrated physical
baseline for the implemented narrow scope. Benchmark PCSE **6.0.13** with
WOFOST72 water-limited classic as a challenger. Keep the existing
`fao56-single-kc-bucket-v1` as the named water-only reference. The decision is
based on conversion/operating fit and verified installed terms, not field
accuracy: no independent yield observations were provided.

The committed [CPU report](../../packages/field-shift-models/reports/cpu-v1.json)
records installed code/license checksums, input/parameter/output hashes,
measured CPU/wall time and peak RSS for Maize and Barley under controlled
wet/dry forcing. Each run is synthetic. Different bundled soil/crop parameters
and PCSE's 2000 demo calendar prevent a paired accuracy ranking; similar output
numbers do not prove agreement with observations. The reference uses its named
existing illustrative Maize coefficient and cannot predict yield. IFS remains
an existing external weather baseline; no live IFS skill benchmark is claimed.

## Exact implementation and asset terms

| Implementation | Evidence and decision |
| --- | --- |
| AquaCrop-OSPy 3.1.0 | Installed wheel's `aquacrop-3.1.0.dist-info/licenses/LICENSE` is Apache-2.0; exact hash and package lock are retained. Bundled crop coefficients are hashed with parameters. Accepted as generic baseline, not locally calibrated agronomy. |
| PCSE 6.0.13 | Installed license states EUPL 1.1 or later, including the later approved EUPL versions. Separate rights/provenance of its demo crop/site assets have not been established for commercial serving; benchmark-only, no commercial default. |
| KU Leuven AquaCrop Fortran 7.3 | [Upstream terms](https://raw.githubusercontent.com/KUL-RSDA/AquaCrop/main/LICENSE) state BSD-3. Not built or selected here; its terms do not license another AquaCrop implementation. |
| Official FAO Python implementation | [Current license](https://raw.githubusercontent.com/un-fao/fao-AquaCrop/main/LICENSE) reserves commercial reproduction rights. Not installed or selected. |
| External WOFOST crop YAML | [Parameter repository](https://github.com/ajwdewit/WOFOST_crop_parameters) is a distinct asset source, not implicitly licensed by PCSE. Not downloaded for serving. |

Terms were inspected on 2026-11-14. Upstream source URLs are context; installed
pinned license hashes in the report identify the actual engine versions tested.
Future engine/data updates require a fresh terms and regression review.
[OSPy documentation](https://raw.githubusercontent.com/aquacropos/aquacrop/master/README.md)
explicitly excludes salinity/fertility/weed/perennial routines; no support is
inferred from the broader FAO AquaCrop family name.

## Conversion and capability boundary

The adapter converts explicit cm horizons to meter compartments without crossing
horizon boundaries, m³/m³ hydraulic values without percentage conversion, and
forcing in the engine's exact positional column order. Ksat and penetrability
are missing from the shared DTO: the host injects pinned, permissioned hydraulic
parameters per immutable field snapshot. They never default to a convenient soil
class. The engine would extend a shallow soil to fit roots; the adapter rejects
that case before execution, requiring maximum root depth plus 10 cm.

A depth-averaged opening VWC is used only with an explicit uniform-profile
assumption, never silently treated as multiple measured sensors. It must cover
the full soil profile, be dated at the start of a UTC day, precede sowing and be
available at the planning instant. Supplied reserve/capacity must agree with
VWC/horizons at that depth. Forcing must include every day from the opening state
through the crop calendars, including winter gaps; missing days are not rain=0.

Maize/Wheat/Barley refer only to the built-in `ospy-generic` / `3.1.0` profile.
Unknown hybrids, missing profiles, unapproved management and outside-configured
footprint return explicit statuses. Required capabilities are checked before
simulation. Rice/paddy and dangerous flood responses remain unsupported; DSSAT
is a possible separately validated rice adapter, not a replacement upland bucket.
Coordinate labels or distance do not alter crop predictions. Solar geometry is
owned by A and is not converted into an invented yield multiplier by B.

Multi-season inference uses one continuous OSPy simulation with
`off_season=True`; it never resets water on January 1. It supports repeated
identical crop/calendars, explicitly rejecting mixed rotations because this OSPy
release exposes no supported rotation calendar. Every actual day closes
soil+surface storage against rain, irrigation, capillary/groundwater inputs,
runoff, deep/tile drainage, evaporation and transpiration, tolerance 1e-6 mm.
Trailing preallocated zero rows after the last harvest are excluded. Season
opening/closing IDs bind the full soil profile to the exact input and trajectory;
consecutive seasons share the boundary ID after the simulated off-season gap.
Final simulation ends at engine harvest, not an invented end-of-year state.

Yield uses the engine's dry-matter t/ha output. Reserve in mm integrates available
water over actual root depth; surface VWC remains at a separate compartment
depth. No missing output is replaced with zero. The engine can genuinely produce
zero yield if a dry seedbed prevents germination; this differs from abstention.
Stress days mean active crop days with transpiration below engine potential,
not a validated yield-loss conversion. Dates and engine versions are evidence.

Without labels, outputs are deterministic per-trajectory points with uncertainty
kind `none`; no interval, coverage or calibrated probability is invented. The
collection is a scenario comparison, not a calibrated predictive distribution.
The model card remains `baseline_only`, `insufficient_training_data`, with null
validated scope. Calibration/learners cannot change it automatically.

## Acceptance ownership and remaining evidence

- G03/G04: B checks model/cultivar capability and winter abstention. A owns the
  artificial early/late thermal-time and sowing feasibility rules.
- G05/G06/G13: daily OSPy conservation and bounded dated irrigation are tested;
  controlled deficit ordering is tested against the named existing bucket.
  Those controlled inequalities are not generalized to real crop yield or margin.
- G07/G08: no fertility bonus; missing fertility response is unsupported. A owns
  crop/history eligibility; C owns irrigation economics and feasibility.
- G09–G11/G20: explicit rice, hazard, unknown cultivar/capability and footprint
  gates prevent unsupported inference or substitution.
- G12/G14: matched physical features give matched numeric predictions without
  field-ID/distance effects. G15 solar reference execution belongs to A.
- G17/G18: zero versus missing, UTC/source chronology and availability checks;
  forecast/oracle datasets are separated in offline evaluation.
- G19/G24: grouped future holdout, positive geographic buffers, train-only
  transforms, frozen gates and synthetic-label exclusion are tested.

These are invariant/contract checks, not completed agronomist review or an
empirical geographic accuracy pack. No independent real-field/year benchmark,
local crop calibration, residual promotion or Prithvi quality comparison is
claimed. Those executions require permissioned labels, a predeclared pilot gate
and (for EO) pinned imagery/embeddings. The empty empirical manifest remains
unchanged. Existing `promoted:false` artifacts remain unchanged.

Feature-worktree verification: **53 package tests passed**; Ruff and formatting,
locked dependency freshness, wheel/source-distribution builds, and frozen CPU
report reproduction passed. Baseline checkout checks passed: **380 API fast
tests**, **73 web tests**, web TypeScript. Engine dependencies emit upstream
pandas/traitlets deprecation warnings, retained rather than silenced in source.
Combined-tree results and published commit IDs are reported in issue #4.

Container builds and live external sources were not run: this lane creates no
Dockerfile, root image inputs or public transport wiring. Empirical calibration,
independent field/year accuracy, local adapter promotion and EO extraction/
quality evaluation remain unexecuted for lack of real labels and pinned imagery.
Issue #4 must not be treated as a scientifically validated local model release.
