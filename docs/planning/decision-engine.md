# Scenario decision engine (lane C, issue #5)

This component compares whole-farm crop sequences over one or three harvest
seasons. It does not train a model, invent yields, change confirmed seed purchases,
or infer probabilities from a set of stress scenarios. It is independently
usable through injected planning-v1 ports; shared runtime mounting belongs to #7.
Frozen base: `ebefa04d9907e5c3819bd23757e032c4e565f6ff`.

## Inputs and economics

`PlanningEngine` resolves every `FieldProfileSnapshot` through `ProfileProvider`,
reads candidates through `SuitabilityProvider`, and evaluates eligible sequences
through `CropModelProvider`. There are no production fixture providers or generic
crop fallbacks. The engine pins and saves the model card, candidates and sequence
evaluations, with a canonical artifact hash. A provider's model reference must
match its pinned card; an unpromoted card cannot label predictions validated.
Scientific release, geographic applicability and physical model validation remain
B's responsibility; C also preserves unsupported/out-of-domain outcomes.

Candidates must cover exactly the requested consecutive harvest seasons, field
snapshots, enabled crop profiles and rulebook. Every assessment must be eligible
with no unchecked rules. History/return, cultivar, paddy, winter, salinity,
fertility, flood and stress-tolerance rules belong to the versioned A/B assessment
and capability boundary. C never replaces them with latitude/country heuristics
or a penalty. Missing sequences/evaluations do not prove infeasibility.

Economics is `area_ha × (yield_t_ha × price_per_t − variable_cost_per_ha −
irrigation_mm × irrigation_cost_per_mm_ha)` for each season and **shared trajectory**.
The v1 convention here is that `variable_cost_per_ha` excludes irrigation. Supply
an explicit zero irrigation rate for prepaid/free water; do not include it twice.
When irrigation is an interval, its upper bound is used as a conservative water
and cost reservation. This is stated scenario accounting, not an expected cost.

A model yield is priced only with `applicability=validated`; otherwise the user
must supply an explicit `yield_origin=user_assumption`. Such assumptions are
stored with their source, date and crop profile and are not model accuracy claims.
Price and yield must share the exact harvest moisture basis. Prices/costs require
currency and date, and the frozen boundary rejects mixed currencies. No exchange
rate is invented. Missing price, yield, cost, water or basis returns an unavailable
margin with null values and evidence. Genuine zero and negative margin remain
valid. A water policy may return a feasible agronomic plan with unavailable money;
a margin policy cannot rank missing money.

Farm totals retain both season and trajectory. All fields experience the same
trajectory ID; neither weather nor market scenarios are independently resampled
by field. `economic_correlation_note` records the supplied shared financial
scenario assumption. Interval sums are scenario ranges, not predictive coverage.
Weights are not used for expectations, chances of loss or CVaR.

## Selection and constraints

`BoundedSelector` exhaustively enumerates combinations of supplied eligible
sequences, using deterministic order and retaining up to three distinct crop
assignment plans. The independent small-farm test oracle enumerates its own
choices/cost formula and rejects the greedy per-field solution under joint
budget/area limits. A single feasible plan remains a single plan.

Policies:

- `reference_margin`: maximize total gross margin across the explicit horizon
  in the named reference trajectory.
- `worst_case_margin`: maximize the minimum horizon gross margin among the tested
  coherent trajectories; this is maximin, not calibrated risk.
- `water_demand`: minimize worst-trajectory total irrigation in mm·ha, subject to
  every upstream agronomic assessment and hard resource constraint.

Hard constraints are profile irrigation limits (mm), farm seasonal water (m³,
1 mm·ha = 10 m³), seasonal budget in normalized currency, crop minimum/maximum
area and maximum share, complete locked assignments/commitment IDs, and dated
equipment capacity. Every constraint is checked across all tested trajectories.
Equipment capacity conservatively reserves full hectares of every overlapping
sowing/harvest operation for the equipment listed on the field. Missing operation
windows abstain; there is no claim of optimal flexible machine scheduling.
Unmodeled equipment requirements must be rejected upstream, not silently assumed.

`optimal` means best within the supplied complete admissible candidate universe,
not an agronomic global optimum. Unknown assessments, model coverage, costs or
windows prevent a proof of optimality/infeasibility. Such a search returns
`feasible` with alternatives or `not_run` without them. A complete contradictory
hard-constraint search returns `infeasible`. A time/node bound returns
`limit_reached`, possibly with feasible alternatives, never a false proof.
Diagnostics retain source/rule/constraint/result IDs; no relevance percentage exists.

## Persistence, scope and application

`RunService` uses the caller's SQLAlchemy transaction. It serializes farm-scoped
operations by locking the existing farm row, then the run. The authenticated
`Principal` supplies the real farm identity; all selected fields are independently
checked against baseline field ownership. Viewers may list/read; only editors or
admins may submit, replan or apply. The isolated router uses existing auth
principal dependencies and commits successful operations before returning.

`SnapshotResolver` is mandatory and has no production default. Its
`resolve(session, request, lock=True)` must return the trusted complete request
and a JSON version vector with `profiles`, `geometry`, `weather`, `rulebook`,
`cultivars`, `models`. Values must identify the exact revisions/checksums, including
model artifact and parameter hashes. The adapter must acquire owning version-row
locks in stable order and hold them until commit, so a concurrent profile/model
update cannot occur between freshness comparison and application. A fake resolver
that merely echoes client inputs is only present in the tests.

Runs persist canonical full inputs with SHA-256, original timestamps and versions;
finished artifacts retain sequence/model provenance and state-carry identifiers.
Same farm/request ID plus the same canonical input returns the same run. Different
input is a conflict. JSON hashing sorts object keys, preserves array order,
normalizes validated DTO output, and rejects non-finite values.

Apply checks human confirmation, path/body scope, completed result, alternative
membership, exact input hash and snapshot list, current authoritative full inputs
and version vector, weather expiry, and existing commitments **in one transaction**.
It creates durable lane-owned crop commitments. It does not mutate the legacy
field's displayed current crop or old rotation preferences. Repeated identical
apply returns `already_applied`; changing its payload or applying the run with a
different request ID is a conflict. Existing commitments cannot be overwritten.
Replan uses a new ID and new immutable draft, preserves all relevant original and
applied commitments, and saves lineage plus changed input sections on its row.
Old snapshots remain unchanged. The lineage is available to integration through
the repository; the frozen `PlanningRun` DTO is not extended silently.

Migration `0012_planning_runs` branches from `0010_precision_plans`. It adds only
C's run/apply/commitment tables, references baseline farms/fields, and has no FK
to A's tables. Upgrade/downgrade tests run on SQLite and isolated PostgreSQL and
verify baseline field preservation. Downgrade removes C's planning data; a
production rollback still requires a tested restore to a fresh database.

## Job and resource handoff to #7

No shared job kind, queue, registry, model import or router mount is changed here.
The following wiring is required before this feature is available in the app:

1. Import C models into the shared metadata registry; mount
   `create_router(RunService(real_snapshot_resolver))` under `/api`, behind the
   existing authentication/CSRF middleware. Add A/B authoritative adapters.
2. Merge the two Alembic heads using `0013_merge_planning` with parents 0011/0012.
   The isolated C branch has one head; the combined parallel lanes need this merge.
3. Submit the run transaction durably before publishing **only its ID** to the
   simulation queue. Use an outbox or periodic queued/expired-run reconciliation
   so a crash between commit and publish cannot strand a run. Geospatial ML uses
   its separate queue. Never send the full weather payload through Redis.
4. Claim and commit before execution. `claim` admits one current attempt with a
   360-second lease, up to three lease-expiry attempts. Completed, failed or
   cancelled runs do not execute again. A fresh execution uses the exact saved
   input. Deterministic input/provider errors are terminal through `finish(error=)`;
   a worker crash is recovered by reclaiming an expired lease. The third expired
   attempt becomes an explicit terminal failure, not an infinite retry.
5. Execute with a cancellable process and **hard 300-second outer deadline**,
   360-second lease and a suitable memory cap. C polls node/time/cancellation
   bounds during search and between provider calls. Synchronous provider calls
   cannot be forcibly interrupted in-process; network/model adapters must enforce
   their own timeouts and the worker process must enforce the hard deadline.
   A killed process must not acknowledge until its recovery policy is durable.
6. Persist result/artifacts via `finish` only for the current live attempt, commit,
   then acknowledge delivery. Late attempts and cancellation are fenced. Connect
   the cancellation predicate to current durable run state. Never convert a
   timeout into infeasibility. Queue redelivery after success is a no-op.
7. Enforce an 8,000,000-byte public body limit **before parsing**, including decoded
   transport bodies, and stream/limit provider outputs. The isolated router also enforces this bound while streaming the body, before
   JSON parsing; service admission additionally checks canonical input size. C admits at most 3000 candidates/evaluations in a
   run and the declared per-field candidate limit. Search is bounded by at most
   1,000,000 nodes/300 seconds; only three retained plans are kept. These are
   resource ceilings, not measured performance/scale promises. Large-payload
   reference transport requires a separately agreed contract extension.

Provider calls, selection and artifacts are reproducible with pinned inputs;
external provider availability is not promised. Private model transport and
release-approved artifact selection remain #7/B responsibilities.

## Evidence and optional explanation

`evidence_pack` serializes scenario totals, objective, reasons and (when supplied)
selected persisted `SequenceEvaluation`s exactly, including nulls, units, ranges,
state IDs, scope and model hashes. Call it with the saved evaluations for the full
pack. Packs have their own hash and include the immutable request hash.

`ExplanationProvider.explain` may return JSON text `{"references": [fact IDs]}`
to order all known facts. C verifies that every reference is present exactly once
and renders only owned deterministic text. New numbers, free prose, unknown
references, dropped facts, invalid JSON or provider failure fall back to the same
complete deterministic facts. The provider receives a deep copy and has no plan
write/apply capability. User text is not used to alter scientific results or
constraints. Guarantees of profit and invented field inundation are not generated.
This deliberately restrictive adapter does not claim to validate arbitrary prose.

## Verification and limits

Reproduce from a locked API venv and locked web dependencies:

```bash
(cd apps/api && .venv/bin/python -m pytest -q -m 'not integration')
ISSUE5_POSTGRES_ADMIN_URL=<disposable-local-admin-url> \
  apps/api/.venv/bin/python -m pytest -q apps/api/tests/scenario_planning -m integration
apps/api/.venv/bin/ruff check apps/api/src/field_shift/modules/rotations/scenario_planning \
  apps/api/tests/scenario_planning apps/api/migrations/versions/0012_planning_runs.py
apps/api/.venv/bin/ruff format --check apps/api/src/field_shift/modules/rotations/scenario_planning \
  apps/api/tests/scenario_planning apps/api/migrations/versions/0012_planning_runs.py
(cd apps/web && npm run typecheck && npm test)
```

PostgreSQL tests create/drop their own uniquely named databases. They do not use
the demo database. They exercise concurrent idempotent submit/apply, stale model
versions, viewer/farm isolation, commitment-preserving replan, lease recovery,
late-attempt fencing and cancellation. Domain tests cover one/three-year inputs,
missing economics/currency/weights, negative/zero results, hard constraints,
limits and provider failure. HTTP tests preserve strict JSON date/number semantics.

G05/G06 are covered at C's irrigation availability/cost boundary. G07–G11,
G19/G20/G22 inject explicitly synthetic A/B abstentions and verify that C preserves
them. G18 rejects future input availability, G21 exercises missing economics and
locked budgets, G23 preserves evidence/intervals/nulls, and G24 keeps fixture and
model release status separate. C does **not** execute B's daily water conservation,
training split algorithms, paddy physics, flood-depth model or field/year accuracy
benchmark. Those predicates remain with their owning lanes. No empirical sites,
new accuracy metrics, calibrated probability or production readiness are asserted.
Docker builds, live providers, paid training and deployment are outside this lane;
shared wiring and end-to-end scientific acceptance remain #7 work.

Lane verification on 2026-11-14: 436 API fast tests passed; 71 lane tests passed
including 15 real PostgreSQL integration tests; 73 web tests and web typecheck
passed. Affected Ruff checks and formatting passed. The same required checks must
run again under the integration lock on the combined jobs tree. The existing
unaffected Redis/provider integration suites were not rerun. These counts are
software verification, not empirical agronomic/model evaluation.
