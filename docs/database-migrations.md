# Database migrations

The API's PostgreSQL schema is managed by Alembic (`apps/api/migrations`). The history has one line up to
`0010_precision_plans`; from there independent lines were developed in parallel and are joined by empty merge
revisions.

| Line | Revisions (from `0010_precision_plans`) | New tables |
|---|---|---|
| Field onboarding (team `main`) | `0011_field_onboarding` | `field_setups` |
| Farmer planning constraints (agent) | `0011_planning_constraints` | `field_planning_constraints` |
| Planning (`nazar-jobs`) | `0011_field_profiles`, `0012_planning_runs` → `0013_merge_planning` → `0014_planning_inputs` | `planning_field_profiles`, `planning_weather_issues`, `planning_v1_runs`, `planning_v1_applies`, `planning_v1_commitments`, `planning_input_bundles`, `planning_input_heads`, `planning_deliveries` |

| Merge revision | Joins | Where |
|---|---|---|
| `0012_merge_agent_constraints` | `0011_field_onboarding`, `0011_planning_constraints` | team `main` with the agent |
| `0015_merge_onboarding` | `0014_planning_inputs`, `0011_field_onboarding` | published planning and onboarding |
| `0016_merge_agent_planning` | `0015_merge_onboarding`, `0012_merge_agent_constraints` | combined planning, onboarding and agent constraints |

Each merge revision changes no table. Every line only creates its own tables, so applying them in any order gives the
same schema.

## Rules

- Never rename a revision id, change its `down_revision`, or edit a released migration. Databases are already stamped
  with these ids: a renamed id fails with "Can't locate revision"; a re-parented one makes Alembic skip tables that were
  never created. Fix a released migration with a new one. `apps/api/tests/test_migration_graph.py` pins the id, parents
  and bytes of every historical revision.
- Keep exactly one head. When two lines meet, add one empty merge revision with a new, unique id; never reuse an id
  another branch may already use for a different merge.
- `alembic upgrade head` is the only deploy step (`compose.yaml`, CI). It works from any earlier state: a database at
  `0010_precision_plans`, at any line's head, or with two heads applied (`alembic_version` with two rows).

## Upgrading an existing database

1. Record the state: `uv run --no-sync alembic current` (from `apps/api`, with `DATABASE_URL` set).
2. Back it up with `infra/backup.sh` (a `pg_dump -Fc` archive), and check the archive lists its tables:
   `pg_restore --list <archive> | grep -c TABLE`.
3. Review the SQL if needed: `uv run --no-sync alembic upgrade <current>:head --sql`.
4. Upgrade: `uv run --no-sync alembic upgrade head`.
5. Check: `uv run --no-sync alembic current` shows the single head, and row counts of the tables you rely on match the
   values from step 1.

## Backup and restore

- An archive restores at the revision it was taken at, and `alembic upgrade head` then brings it forward with its data.
  A head archive restores at head; CI's "Restore a PostgreSQL archive" step checks this on every integration run.
- `pg_restore` re-prints CHECK constraints with different cast placement (for example
  `ANY (ARRAY[('a'::character varying)::text, ...])`). The constraint is the same and still enforced, but a textual
  `pg_dump --schema-only` diff against a never-restored database shows it; compare restored with restored.
- Restore into an empty database (`createdb`, then `pg_restore --exit-on-error`), never over a live one.

## Downgrades: not a rollback

- Downgrading below a line drops that line's tables and their data. Roll a deployment back by restoring the backup from
  step 2, not with `alembic downgrade`.
- `alembic downgrade <one line's head>` from a merged head only removes the merge revision: the other lines stay
  applied and `alembic_version` lists several heads again. To remove everything after the branch point, downgrade to
  `0010_precision_plans`.
- Downgrades exist for development and tests (`apps/api/tests/integration/test_migration_upgrade_paths.py` checks that
  head → branch point → head returns the same schema on an empty database).

## Tests

- `uv run --no-sync pytest -q tests/test_migration_graph.py`: one head, history unchanged, merges empty.
- `FIELD_SHIFT_TEST_DATABASE_URL=postgresql+psycopg://… uv run --no-sync pytest -q -m integration tests/integration/test_migration_upgrade_paths.py`:
  a fresh install, and every branch point, shipped head and pair of unrelated heads upgraded to head on PostgreSQL with
  their data kept and the fresh-install schema, repeatable; downgrade to the branch point and back.
