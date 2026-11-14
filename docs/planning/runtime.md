# Planning runtime and pilot boundary

Issue #7 connects the immutable field profiles and agronomic screening from #3,
the versioned CPU crop provider from #4, the joint selector from #5 and the
workspace from #6. The old Fields, Water, Rotations, Farm and Agent routes remain
available. Open `/rotations?planning=1`; use `&desktop=1` for the wide layout.
The field, season and weather trajectory remain in the route when navigating.

This is an **uncalibrated scenario pilot**, not a validated profit forecast.
Missing profiles, reviewed inputs or the private model produce an explicit setup
or unavailable state. No demo request is installed at application startup.
The only synthetic installer is in `apps/api/tests/planning_runtime` and requires
an explicit test-database flag. Do not run that installer on a real farm database.

## Inputs and supported computation

1. Create/edit a profile for each registered field. Geometry and area come from
   the registered field. Unknown history, soil or water remain unknown.
2. Obtain permitted, dated weather trajectories, reviewed regional crop rules,
   crop parameters and soil hydraulics. Preserve their origin and availability
   time. Long horizons are scenarios, not daily weather predictions years ahead.
3. Review a private `InputBundle` JSON using the schema in
   `apps/api/src/field_shift/modules/planning_runtime/inputs.py`. Its `template`
   is the frozen `FarmPlanningRequest`; other keys are `rulebook`, per-field
   `regions`, per-snapshot `hydraulics`, optional geographic `footprint`,
   `calendars`, `provenance` and `synthetic`. Required data are described in
   [model data requirements](model-data-requirements.md).
4. Import the bundle with the local operator CLI. The importer checks current
   profile revisions, weather archive consistency and the actual model card.
   It hashes the whole bundle and model card; activating a new bundle does not
   erase old versions or saved calculations.
5. Select fields, crops, goal and explicit economic/resource assumptions in the
   workspace. Calculate, inspect evidence and alternatives, then confirm apply.
   The server stores commitments. Replanning preserves their exact identifiers
   and assignments and displays changes from the previous result.

The baseline adapter is AquaCrop-OSPy 3.1.0 for generic Maize, Wheat and Barley
(`ospy-generic`, profile `3.1.0`), subject to its checks. It currently supports
repeated crops/calendars; **mixed rotations abstain**. PCSE remains an offline
challenger, not an automatically selected production fallback. A supported crop
name does not imply validated performance in a region or cultivar.

OSPy requires exact operation dates. `calendars` supplies `field_id`, `crop`,
`season`, `sowing_date`, `harvest_date`, `source`, `available_at`. The adapter only
narrows reviewed permissible windows, rejects dates outside those windows and
leaves locked decisions unchanged. Missing exact dates are not invented. Supply
continuous daily weather including gaps between seasons, a compatible initial
root-zone state and explicitly sourced hydraulic parameters per soil horizon.

Only independently validated yields can automatically price model-based margins.
For baseline mode, the user must supply yield assumptions and their basis along
with prices/costs. Both the assumed margin and the simulated water/yield evidence
remain distinguishable. No probability calibration, profit uplift, local model
accuracy, flooding depth or political-risk capability is claimed.

## Optional private service

Set `FIELD_SHIFT_CROP_MODEL_TOKEN` to a new random value of at least 32 characters
in the ignored environment file; never put it in a bundle or source control.
`FIELD_SHIFT_CROP_MODEL_URL` defaults to `http://crop-model:8001` inside Compose.
Use a unique project, browser port, image tags and backup directory for each
worktree; examples below assume these are set in a private environment file.

```sh
docker compose --env-file /path/to/private.env -p your-project --profile crop-model build api web crop-model
docker compose --env-file /path/to/private.env -p your-project --profile crop-model up -d
# Operator-reviewed input only, outside Git. This changes the selected farm's inputs.
docker compose --env-file /path/to/private.env -p your-project --profile crop-model run --rm --no-deps \
  -v /path/to/inputs.json:/inputs.json:ro api \
  python -m field_shift.modules.planning_runtime.cli /inputs.json
```

The model is on an internal network, has no published port and requires the
private token. Its numerical dependencies use the separate
`packages/field-shift-models/uv.lock`; the API does not install crop/training
engines. `/health` checks the server/token configuration; admission additionally
loads and verifies the actual model card. A busy or unavailable runtime never
silently supplies estimates. Use team access before exposing the application;
the planning endpoints use the existing session/CSRF and role checks.

## Execution, failure and versioning

| Boundary | Limit / behavior |
| --- | --- |
| Public planning request | 8 MB, bounded contracts; encoded bodies rejected |
| Planning queue | Separate `planning`, concurrency 1, worker 1 CPU / 1536 MiB |
| Execution child | 300 s wall and CPU, 1 GiB address space, 64 MiB file output |
| Celery task / lease | 330 s hard task bound / 360 s lease; at most 3 claimed attempts |
| Private model | One child; 60 s wall / 30 s CPU, 2 GiB address space, 16 MiB input/output |
| Model container | 1 CPU, 2304 MiB, read-only root, 128 MiB temporary space, 64 processes |
| Publication retry | Durable delivery record, every 30 s if queued or lease expired |

Submission and delivery intent commit in one PostgreSQL transaction before a
Redis identifier is sent. Publication failure is recoverable from PostgreSQL.
A crashed worker is reclaimed after lease expiry; duplicate deliveries do not
create duplicate results. Completion is fenced by attempt/status/lease and is
committed before acknowledgement. Cancellation prevents results from being
published and terminates the execution child; a private evaluation already in
flight remains independently bounded by its 60-second limit. Deterministic
model/input errors are terminal; create a new request after fixing the cause.

The version vector includes exact profile/geometry revisions, full weather
issue/cycle/lead content, rules, crop/cultivar parameters, model identity and
input bundle hash. Profiles include soil, history and management. Commitments
are checked again at submit/apply. The complete request hash includes decisions
and assumptions. Changed profiles, boundaries, inputs or artifacts require a
fresh reviewed bundle/calculation; an old result remains readable. There is no
unversioned prediction cache or automatic promotion of failed models.

Migrations 0011 and 0012 join at 0013; 0014 stores immutable bundles, their active
heads and durable deliveries. Never run downgrade on the only live database.
Restore a backup into a fresh database, check records, and only then plan a
switch or schema rollback. `git revert` alone does not restore database contents.

## Reproducible verification

Install locked API/model environments, web/agent frontend npm dependencies and
Playwright Chromium, then run `python3 infra/verify-planning.py`. It creates a
unique Compose project and private credentials, builds unique images, performs
an authenticated two-field three-season flow with real CPU inference and
**explicitly synthetic inputs**, checks desktop/mobile confirmation and replan,
restarts services, restores a PostgreSQL dump into a fresh database, tests
schema rollback there and runs all PostgreSQL/Redis integration tests. Finally
it checks model outage behavior and removes only its own containers/volumes.

Logs, screenshots and `report.json` go to
`/tmp/field-shift-planning-verification` (override `PLANNING_VERIFY_OUTPUT`).
The report separates technical checks from empirical validation. `--no-build`
requires all three explicit `PLANNING_VERIFY_{API,WEB,MODEL}_IMAGE` tags. It never
uses the user's running project or `.env`. Existing isolated project names are
rejected instead of overwritten. No paid model requests or GPU training run.

See [data audit](data-audit.md) for the observations still needed and preserved
negative release evidence. Synthetic G01–G24 and browser tests establish
invariants and UI behavior, not agronomic accuracy.
