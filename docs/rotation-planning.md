# Farm rotation planning

The Rotations route includes a farm planner. It assigns whole selected fields to
crops for three seasons. Generate saves an immutable draft; the separate
confirmation action atomically replaces Plan A and makes it the main plan for the
selected fields and planning period. Actual current crops and geometries are not
rewritten by a future plan.

The [standalone scenario API](../research/rotation-handoff/README.md) contains
the Sutherland replays and the [handoff for UI and assistant developers](../research/rotation-handoff/UI_AND_ASSISTANT.md).
Run it from its own directory. Its climate routes require Ruslan's water-balance
engine. The main application has a separate farm planner and API.

## Inputs and rules

- Up to 30 fields, seven named crops, and three consecutive planning seasons.
  The first season must be the active farm season or later. Current whole-field
  area is explicitly used for planning, including where only part of a field is
  currently planted; historical plans are not computed from current boundaries.
- Per-crop minimum intervening years, preference from 0 to 10, minimum hectares,
  maximum hectares and maximum share of the selected area. Area limits apply in
  every planning season, not only to the sum of three seasons.
- Per-field locked crop decisions and up to eight preceding years of manually
  supplied crop history. Known current crop and its actual recorded season are
  included automatically. Future saved plans are not presented as actual past
  planting records. Missing history remains unknown and is reported.
- Optional prohibition of consecutive crops in the same botanical family, and
  an option to require at least two different crops within each three-year field
  sequence. Wheat, maize and barley are Poaceae; soy and peas are Fabaceae.

The return-break value counts *intervening* years. A value of 1 permits wheat in
2026 and 2028, but not 2026 and 2027. The default is an editable planning
constraint, not a crop-specific regional agronomic recommendation. A crop family
rule is deliberately separate from the same-crop return interval.

General rotation principles are grounded in [NRCS crop rotation 328](https://www.nrcs.usda.gov/resources/guides-and-instructions/conservation-crop-rotation-ac-328-conservation-practice-standard)
and [University of Minnesota small-grain rotation guidance](https://extension.umn.edu/agriculture/crop-production/small-grains/small-grain-crop-rotations).
These sources do not validate the planner's chosen constraints for a Ukrainian
farm. Local crop suitability, pests, pesticide carry-over, planting dates, labor
and equipment must be reviewed by the farm; no yield, profit, water-risk or
fertilizer quantities are invented by this optimizer.

## Solver and resource bound

The deterministic branch-and-bound solver enumerates at most 343 possible
three-year sequences per field, filters against local history and locks, then
allocates sequences across fields while checking annual area limits. It uses
remaining feasible crop area for quota pruning and a score upper bound.

The transparent objective is the sum of `crop preference × field hectares` over
the three seasons. All configured constraints are hard. No penalty permits an
otherwise forbidden sequence. A stable field/crop order breaks ties. The API
reports objective units as `preference_points_ha`, never as a probability.

Search stops after 120,000 visited nodes/candidate attempts. Its result is one of
`optimal` (upper bounds/exhaustive search proved the best score), `feasible`
(valid allocation found before the budget ended, optimality unknown),
`infeasible` (constraints proved inconsistent), or `search_limit` (no allocation
found within the budget; infeasibility is not proven). No solver service,
external ML calls, downloads or package dependencies are needed. It is a bounded
synchronous planning request, not a Celery task.

## Persistence and concurrency

`farm_rotation_drafts` stores input constraints, field and existing preference
snapshots, result and UTC timestamps. A UUID request key allows safe retry after
a lost response. Reusing that key with different inputs returns a conflict.
Generating a draft does not change a field's current main plan.

Applying locks the farm row and selected fields in stable order, checks the full
field/preference snapshot (including boundary revision, area, current crop and
its season) and writes all main plans in one database transaction. A stale
snapshot rejects the entire apply; no subset of fields changes. Replaying an
already applied draft returns its original confirmation without overwriting
subsequent manual edits. Saved draft listing returns the newest 50 drafts.

The existing access guard controls these routes. Viewers may inspect saved drafts
and context; editors and administrators may generate and confirm plans. Farm
scope follows the host application's active farm. The planner does not grant
independent permissions.

## API

- `GET /api/rotations/planning/context?start_season=2027`
- `GET /api/rotations/planning/drafts`
- `POST /api/rotations/planning/drafts`
- `GET /api/rotations/planning/drafts/{uuid}`
- `POST /api/rotations/planning/drafts/{uuid}/apply`

Migration `0009_rotation_planning` follows `0008_team_access`. Fast tests cover
an independent exhaustive optimum, area indivisibility, history and family
constraints, incompatible locks, explicit search-budget outcomes, retries,
field/farm scope, atomic stale-apply rejection and reversible migration data
preservation. PostgreSQL concurrency should also be exercised in the integration
environment before production rollout.
