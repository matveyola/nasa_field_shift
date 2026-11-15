# Planning-v1: frozen boundaries for issues #3–#6

Contract version: **1.0.0**. Prerequisite: [#2](https://github.com/matveyola/nasa_field_shift/issues/2).
Research and product scope: [#1](https://github.com/matveyola/nasa_field_shift/issues/1).

This pack defines data exchange, validates structural and cross-field invariants,
and supplies explicitly synthetic examples. It does not implement crop models,
agronomic recommendations, new HTTP endpoints, training or measured accuracy.

The canonical definitions live in `apps/api/src/field_shift/contracts/planning_v1`.
Pydantic exports JSON Schema 2020-12; the existing pinned
`services/agent/package-lock.json` toolchain generates
`apps/web/src/api/planningV1.generated.ts`. Do not hand-edit generated schemas or TS.
No new runtime dependencies or shared locks are introduced.

## Start the four lanes

The personal integration branch is **`nazar-jobs`**, not `main`. Follow
[`$issue-worktree`](../../.agents/skills/issue-worktree/SKILL.md).
After #2 is published, create issue worktrees from the same published contract commit:

```bash
git fetch origin
contract_sha=$(git log -1 --format=%H origin/nazar-jobs -- contracts/planning-v1/README.md)
git worktree add -b nazar-issue-3-field-profile ../nasa_field_shift-worktrees/nazar-issue-3 "$contract_sha"
git worktree add -b nazar-issue-4-crop-models ../nasa_field_shift-worktrees/nazar-issue-4 "$contract_sha"
git worktree add -b nazar-issue-5-scenario-planning ../nasa_field_shift-worktrees/nazar-issue-5 "$contract_sha"
git worktree add -b nazar-issue-6-planning-ui ../nasa_field_shift-worktrees/nazar-issue-6 "$contract_sha"
```

Run each command once; inspect existing branches/worktrees instead of recreating them.
The completion record on #2 publishes the exact contract SHA. Prefer that SHA if
this README later changes. Each agent can develop and test with typed ports and
fixtures without another lane's implementation. Final wiring belongs to #7.
Merge and push through the skill's shared integration lock; no force pushes.

## Data ownership and flow

| Producer | Contracts | Consumer |
| --- | --- | --- |
| A | `FieldProfileSnapshot`, `WeatherScenarioSet`, `SuitabilityAssessment`, `CandidateSequence` | B, C, D |
| B | `ModelCard`, `SequenceEvaluation`; ingests `TrainingObservation` | C, D |
| C | `FarmPlanningRequest`, `PlanningRun`, `FarmPlanComparison`, `PlanAlternative` | D |
| D | `ProfileWriteRequest`, `FarmPlanningRequest`, `ApplyPlanRequest`, `ReplanRequest` | A, C through authenticated adapters |

[`ports.py`](../../apps/api/src/field_shift/contracts/planning_v1/ports.py) specifies
synchronous domain ports. Adapters may dispatch asynchronous durable jobs;
the public request must not block on training or expensive inference.
`PlanningContracts` is an export/test index, never an HTTP body. Each named DTO
has its own definition under `$defs` in the schema bundle.

`CandidateSequence` can retain incomplete/rejected assessments for diagnostics.
C may select it only when all mandatory rules are eligible and model capabilities
are satisfied. Supporting a species ID does not establish cultivar validity.
Unknown crop/cultivar IDs remain representable so A/B can return `unsupported`.
Rice exists in the new registry; the legacy planner's Crop enum is unchanged.

## Units, coordinates and time

- Geometry is GeoJSON Polygon, WGS84/EPSG:4326, **longitude then latitude**, decimal
  degrees. Rings are closed and nondegenerate. A must additionally validate full
  topology, holes, area and land coverage before persisting. Split antimeridian
  fields upstream; MultiPolygon and greenhouses are outside this v1 scope.
- Areas are hectares. Soil depths are cm below the surface. Moisture fractions
  are **m³/m³**, never percentages; root-zone plant-available reserve/capacity are
  **mm**. They are not interchangeable without a soil/depth calculation.
- Soil sand/silt/clay are fractions, carbon g/kg, salinity dS/m, pH dimensionless.
  Texture sum tolerance is 0.02 for rounding; unknown horizons stay unknown.
- Daily forcing is minimum/maximum °C, liquid rain mm/day, FAO reference ET₀
  mm/day, shortwave MJ/m²/day, wind m/s, relative humidity as a fraction.
  Snow water equivalent is a separate mm value. The schema never interprets
  absent snowfall or a missing day as zero.
- Solar descriptors use UTC instant, zenith 0–180°, aspect clockwise from true
  north [0,360), slope 0–90°. Flat terrain has null aspect. Instantaneous surface
  irradiance W/m² is distinct from daily integrated MJ/m². No latitude/aspect
  yield multiplier is part of this contract.
- All timestamps require explicit UTC (`Z` or `+00:00`). Daily `date` is the UTC
  aggregation date; providers must document any local-day conversion. Seasons
  are **harvest years**, allowing winter sowing in the preceding calendar year.
- `acquired_at <= available_at <= retrieved_at`; source acquisition describes the
  observation/run product, not a future forecast valid date. Planning inputs must
  have `available_at <= as_of`. Retrieval may be later in a historical replay.
- Weather retains cycle, issue, model version, member/trajectory, grid footprint,
  native resolution and valid dates. Forecast leads are UTC calendar-day offsets
  from cycle. Historical replay additionally preserves original `source_date`.
  Reanalysis is not an archived operational forecast.
- Every trajectory covers the same fields and complete declared daily window.
  Shared trajectory IDs preserve cross-field weather dependence. Weight null
  means **no calibrated probabilities**. Complete weights require documented
  calibration and sum to one; a string alone is not scientific validation.
- `stale_after` defines latest usable planning instant. An old snapshot is kept
  for reproducibility; applying it requires current revision checks by C.

## Statuses and uncertainty

Suitability: `eligible`, `ineligible`, `insufficient_data`, `out_of_domain`,
`unsupported`, `error`. Output metrics additionally distinguish `not_applicable`.
Every unavailable metric has null values and evidence-linked reasons, not a
fabricated zero. Measured zero rain, zero budget and negative gross margin are
valid values. Available ranges are ordered, and point estimates lie within them.

Training: `not_trained`, `insufficient_training_data`, `trained`, `failed`.
Job lifecycle: `queued`, `running`, `succeeded`, `failed`, `cancelled`.
Solver: `not_run`, `optimal`, `feasible`, `infeasible`, `limit_reached`, `error`.
These are separate domains: a successfully executed job can return an infeasible
comparison. A limit may return feasible alternatives without claiming optimality.

Scenario range is not a calibrated predictive interval. Predictive intervals
require coverage, method version and calibration scope. `ModelCard` records exact
artifact/parameter/license checksums, commercial-use status, capabilities, cohort
metrics, partition group IDs, release protocol and fallback. Fit/calibration/test
farm and grid groups cannot overlap. B must additionally validate future-year
splits, geographic buffers, metric adequacy and actual release evidence; passing
the schema is not a promotion decision. No existing artifact gates change here.

Multi-year evaluations carry state identifiers between consecutive available
seasons. B owns actual water conservation, daily transitions and missing winter
capability handling. Root and surface moisture always retain their respective
depths. Harvest yields require dry/fresh/standard moisture basis, including the
moisture fraction; monetary assumptions must use a compatible harvest basis.

## Economics, limits and application

Scenario gross margin per hectare is price × yield − variable costs; C adds
explicit irrigation costs if not already included in its documented cost basis.
It must avoid double-counting irrigation. This is not net profit. Farm scenario
results aggregate currency across hectares, with the season and trajectory retained.
Normalize currencies explicitly upstream; no implicit exchange rate exists here.
Missing price/cost/yield produces unavailable economics. User-entered yields are
labeled `user_assumption`; an LLM cannot supply missing model results.

Goals: reference-scenario margin, worst tested scenario margin, water demand.
Locks carry commitment IDs. Budgets, farm irrigation volumes (m³), crop areas/shares
and dated equipment capacity are explicit constraints. Conflicting valid inputs
are accepted as a request and must produce an infeasibility explanation, not be
silently relaxed or altered by an agent.

Structural bounds: 1–30 fields, one to three planning seasons, 1–64 weather
trajectories, up to 2192 daily records per trace, 1–3 alternatives. Solver limits
are 1–1,000,000 nodes, 1–300 seconds, at most 1000 candidates per field. These are
contract ceilings, not performance promises. C/#7 must enforce stricter measured
payload/CPU limits and use persisted immutable snapshots rather than duplicate
large weather arrays over the network. B must enforce its own inference resource
limits; training is an explicitly separate operation.

`request_id` is scoped to farm + operation for idempotency. Same ID with a different
canonical payload is conflict. The owning service resolves/authenticates source
snapshots; client-supplied farm IDs or model cards never confer authority.
Hashes use SHA-256 of the persisted, canonical input JSON (sorted object keys,
compact separators, finite values, UTF-8, normalized validated model output).
The service computes hashes; it does not trust a client's asserted hash.

`ProfileWriteRequest.expected_revision = null` means create-only; otherwise CAS
against the current revision. `ApplyPlanRequest` requires explicit human
confirmation, expected input hash and profile/geometry revisions. C must compare
all current input versions, including weather, rules and models, in the same
transaction as apply. Stale/forbidden/error responses have no applied timestamp.
Replan creates a new request and preserves the referenced commitments; it does
not mutate old snapshots or buy compliance by unlocking decisions.

## Transport handoff (implemented by A/C, mounted by #7)

The prefix below is reserved; no routes are added in this issue. Use the existing
authentication/farm access dependencies, not new header-based identity.

| Operation | Reserved route | Request → response |
| --- | --- | --- |
| Read profile | `GET /api/planning-v1/farms/{farm_id}/fields/{field_id}/profile` | → `FieldProfileSnapshot` |
| Write profile | `PUT` same path | `ProfileWriteRequest` → `FieldProfileSnapshot` |
| Submit plan | `POST /api/planning-v1/farms/{farm_id}/runs` | `FarmPlanningRequest` → `PlanningRun` (202) |
| List plans | `GET` runs path | → `PlanningRun[]`; bounded pagination defined by C |
| Read plan | `GET .../runs/{run_id}` | → `PlanningRun` |
| Apply | `POST .../runs/{run_id}/apply` | `ApplyPlanRequest` → `ApplyPlanResult` |
| Replan | `POST .../runs/{run_id}/replan` | `ReplanRequest` → new `PlanningRun` (202) |

Path farm/field/run IDs must match body scope. Serialize predictable errors as
`PlanningError`; 403 forbidden, 404 inaccessible/not found, 409 stale/idempotency
conflict, 422 validation, 503 unavailable. Never report apply success on an error.
A and C own isolated routers. #7 owns mounting, public body-size limits, durable
job registration and private model-service transport. Large-input reference-based
submission/pagination envelopes, if required, must be proposed as an explicit
contract extension before D relies on them; no lane silently forks this v1.

## Geographic specifications and empirical evidence

`cases.v1.json` preserves the 24 original issue specifications. It stays marked
`test_specification_not_executed`: this issue validates their **shape, references
and fixture expansion**, not the future domain predicates.

`case-bindings.v1.json` binds every invariant to a stable predicate ID, owner,
versioned controlled parameters and required model capabilities. Thermal-time
thresholds there are artificial test profiles, not recommendations for actual
cultivars. Each domain lane records its engine/rule/reference version in a
separate execution report. Production use additionally needs agronomist review
and empirical evaluation; no test parameter is a production default.

`weather-fixtures.generated.json` expands only explicit recipe windows plus G03
and G04's explicit windows, with inclusive daily dates and missing optional
variables retained. G01's cold recipe has **no implicit full-year weather**; A/B
must declare a window before running that domain case. G12/G14/G16 weather tokens
are relational fixture specifications, not unresolved provider IDs to fetch.

`empirical-sites.v1.json` is separately empty with `insufficient_data`. Acquired
sites must include polygon, issue-snapshot and observation locators/checksums,
permissions and acquisition time. Never populate it with these synthetic cases
or invent harvest/accuracy labels.

## Ownership and migrations

`ownership.v1.json` is the machine-readable allowlist. Frozen contracts, fixtures,
generated TS and contract tests are read-only for all four lanes.

| Owner | Exclusive work | Migration |
| --- | --- | --- |
| A / #3 | `fields/planning_profile`, `rotations/suitability`, their tests, spatial docs | `0011_field_profiles` from `0010_precision_plans` |
| B / #4 | `packages/field-shift-models`, optional `services/crop-model`, model docs | None |
| C / #5 | `rotations/scenario_planning`, its tests, decision docs | `0012_planning_runs` from `0010_precision_plans` |
| D / #6 | web planning + field-profile features, API adapter, own tests/E2E, UX docs | None |
| #7 | shared registries, jobs, locks, Compose, CI, App/Shell/routing, status/roadmap | merge `0013_merge_planning` with parents 0011 + 0012 |

A/C tables may reference baseline farms/fields, not each other's new tables.
Each feature branch has one valid migration head; #7 restores one merged head.
Existing files outside allowlists remain read-only. Shared fixes and contract
revision requests are coordinated separately, never hidden in a lane's diff.

## Isolated worktree environments

Create dependencies per worktree using the committed locks. Do not commit `.env`,
client observations, caches or model binaries. Do not reuse the running demo's DB.

```bash
# Example for lane A, from its own worktree; choose distinct values for B/C/D.
export COMPOSE_PROJECT_NAME=fieldshift-nazar-issue3
export WEB_PORT=18083
export API_IMAGE=field-shift-api:nazar-issue3
export WEB_IMAGE=field-shift-web:nazar-issue3
export AGENT_IMAGE=field-shift-agent:nazar-issue3
docker compose -p "$COMPOSE_PROJECT_NAME" config --quiet
docker compose -p "$COMPOSE_PROJECT_NAME" up -d postgres redis
```

Use 18084/18085/18086 and corresponding issue-specific image/project names for the
other lanes. Project-prefixed named volumes isolate Postgres, Redis and backups;
inspect `docker compose config` for external/bind volumes before use. Never run
`down -v` on another project's name. Add only the needed services to avoid costly
unrelated model launches. Retain one build target per shared API image.

For host-based tests use a separate database (e.g. `fieldshift_nazar_issue3`) and
a task-specific connection URL supplied locally; schema isolation also requires
its own search path and Alembic version table. Ports and container names alone
do not isolate DB state. Do not publish credentials in terminal reports.

## Reproduce validation

With existing locked API, web and agent schema-tool dependencies installed:

```bash
apps/api/.venv/bin/python -m field_shift_contracts.planning_v1.export --repo-root . --check
apps/api/.venv/bin/python -m pytest -q apps/api/tests/planning_v1_contracts
apps/api/.venv/bin/ruff check apps/api/src/field_shift/contracts apps/api/tests/planning_v1_contracts
apps/api/.venv/bin/ruff format --check apps/api/src/field_shift/contracts apps/api/tests/planning_v1_contracts
node contracts/planning-v1/check.mjs
apps/web/node_modules/.bin/tsc -p contracts/planning-v1/tsconfig.json
# Integration checks:
(cd apps/api && .venv/bin/python -m pytest -q -m 'not integration')
(cd apps/web && npm run typecheck && npm test)
```

Regenerate using the export command without `--check`. Export hashes are recorded
in `export-manifest.json`. The TypeScript example uses `satisfies`, not a type cast;
negative type tests and Ajv runtime checks exercise invalid payloads too.
JSON Schema/TS validate structure and scalar bounds; Python additionally checks
cross-field chronology, scope, units and references. Browser validation is not
an authorization or scientific validation boundary.

Do not change schemas or cases merely to pass a lane's tests. An agreed change
updates canonical definitions, generated files, examples, compatibility notes
and version together, then publishes a new shared commit before dependent work.

### Two-season request compatibility

Planning-v1 now also accepts `season_count: 2`. Existing one- and three-season
payloads and their stored values are unchanged; the wire version remains 1.0.0.
This expands request capacity, not model applicability: source coverage, reviewed
calendars, crop support and model resource limits still govern every evaluation.
No four-or-more-season model support is implied.

### Explicit land provenance (compatible optional extension)

`FieldProfileSnapshot.land_source_ids` optionally names the existing source records
that support `land_status`. It defaults to an empty list for legacy profiles;
references must resolve and be unique. No source is automatically assigned from
soil/weather/history provenance or inferred from provider/product text. Empty
bindings are omitted when serializing so pre-extension canonical snapshot and
profile-write request hashes stay unchanged. Nonempty bindings serialize and
participate in those identities. The schema version remains 1.0.0 because this is
an optional, backward-parseable addition; older consumers that reject unknown
properties must be updated before consuming newly bound profiles.

The rotation handoff gate requires at least one explicitly linked, nonsynthetic
source with `quality=valid`, `coverage_fraction=1`, and polygon or area-weighted
spatial coverage. Partial/missing/zero/unknown coverage and point-only evidence do
not establish whole-field arability. Current geometry, farm scope and source
availability checks still apply; legacy unbound land assertions remain unknown in
that gate. Qualified non-arable/ice/ocean assertions still block submission. This
is an evidence link and quality policy, not independent verification of source
contents or crop suitability. In particular, `kind=user_entered` remains a
user-reported assertion; an authenticated import does not make it an observed,
validated field measurement. No yield/thermal/model release gate is promoted.
