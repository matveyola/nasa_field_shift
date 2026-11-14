# Test execution for Field Shift

Read current `.github/workflows/ci.yml`, package manifests and test configuration before execution. This matrix is a starting inventory, not permission to skip newly added suites. `packages/field-shift-models` and the planning integration are checked by the CI planning job.

## Modes

- **affected:** directly affected tests, Ruff on changed Python, and relevant TypeScript/contract checks. Widen for changed public interfaces, authentication, migrations, jobs or data pipelines.
- **fast:** all unit suites, lint/format checks, validators and typechecks below. At integration/review, add relevant builds and impacted integration tests.
- **full:** fast, all web/widget production builds, API integration, deterministic widget browser E2E, both Compose validations, plus affected Docker builds and migration/restore checks. Inventory all suites first. Report unavailable and opt-in checks separately; full is not green if a required suite was skipped.

Run commands in the listed directory. Install only when necessary, from locks, with exclusive ownership of the environment. Do not run installers concurrently with checks using that environment. Avoid `uv run` without `--no-sync` when an explicitly prepared environment is expected.

The executable entrypoint is `python3 infra/verify.py --mode fast --build` from the repository root; use `--list` to inspect its ordered commands without running them. It checks contract validators/types before consumer builds and checks, then lint and unit suites, and exits nonzero on failure. `--mode tooling` checks the agent configuration and orchestration helper tests without application dependencies. Neither mode claims integration or E2E coverage. Both web/widget build scripts already invoke TypeScript; keep that existing build behavior.

| Directory | Locked setup | Checks |
| --- | --- | --- |
| `apps/api` | `uv sync --locked --extra dev` | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .`; `uv run --no-sync pytest -q -m "not integration"` |
| `packages/field-shift-ml` | `uv sync --locked` | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .`; `uv run --no-sync pytest -q` |
| `packages/field-shift-models` | `uv sync --locked` with Python `>=3.12,<3.13` | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .`; `uv run --no-sync pytest -q` |
| `services/agent` | `uv sync --locked` | `uv run --no-sync pytest -q` |
| `apps/web` | `npm ci` | `npm test`; `npm run typecheck`; `npm run build` |
| `services/agent` | `npm ci --ignore-scripts` | `npm run check:ts` |
| `services/agent/frontend` | `npm ci --ignore-scripts` | `npm run check:validators`; `npm test`; `npm run typecheck`; `npm run build` |

The private agent Python lock does not declare Ruff. For affected Python there, use an available repository Ruff executable on explicit paths and record its configuration; do not pretend `uv run --no-sync ruff` is supplied by that package.

For targeted pytest use file paths/node IDs. For web tests use the actual manifest's Node test command narrowed to affected files. For widget unit tests use `npm test -- <affected.test.tsx>`. Do not auto-update snapshots, regenerate validators or auto-format during verification; report drift to the implementation owner.

## Integration and browser checks

At repository root:

```bash
docker compose config --quiet
docker compose --profile agent config --quiet
```

At `apps/api`, run `uv run --no-sync pytest -q -m integration` with `FIELD_SHIFT_TEST_DATABASE_URL` pointing to a disposable PostgreSQL database and `FIELD_SHIFT_TEST_REDIS_URL` to a dedicated empty Redis database. Fixtures create/drop schema and mutate data. Missing URLs can result in skipped tests with exit code zero; inspect counts and reasons. The Compose topology test also skips without Docker Compose.

At `services/agent/frontend`, run `npm run test:e2e` with the required Playwright browser installed. Default replay fixtures are deterministic. The checked-in Playwright config uses port 5173 and `reuseExistingServer: true`: serialize runs and verify the server belongs to the exact tested checkout, or use an explicitly isolated config/port. If the port belongs to the user, do not stop their server or claim it tests this revision.

Worktrees do not isolate PostgreSQL, Redis, ports, Docker projects, volumes or image tags. Allocate those resources per run; builds can overwrite default image tags used by the running stack. Use existing infrastructure procedures and isolated resources for affected Docker builds. For migration and backup changes, follow the integration job's fresh-database restore procedure and validate restored data. A code revert is not a database rollback test.

## Optional execution and reporting

For deterministic browser runs, inspect and unset inherited `FS_E2E_LIVE`, `FS_E2E_OFFLINE` and `FS_DESIGN_SHOTS` in the child process unless explicitly in scope. Do not silently enable them. Live providers, optional embeddings/dense/reranker downloads, expensive training and model evaluation require appropriate task scope and resources. List these separately from deterministic suites. A broad test request does not silently authorize paid provider traffic.

Keep logs under a task-specific temporary directory outside delivered source. Return one row per command: checkout/revision, command, exit status, duration, counts/skips and log path. Record missing dependencies or unavailable services as limitations, not successful checks. Check that tracked and untracked deliverable source did not change during execution.

For agent orchestration changes, validate TOML, role discovery, skill metadata and links, plus realistic delegation behavior. Application suites need not run when their inputs and boundaries are unchanged. When changing the existing integration helper, run its own tests under `.agents/skills/issue-worktree/` as well.


## Planning integration boundary

Fast checks include planning-v1 generated export freshness, JSON case/invalid-payload
checks, TypeScript contract boundaries and private crop-runtime lint/format before
consumer verification. Install `services/agent` npm dependencies as well: planning
export uses its locked json2ts tool; never substitute a globally installed generator.

Full checks additionally run `npm run test:planning` in `apps/web` (isolated port
18086), the frozen CPU benchmark from `packages/field-shift-models`, and
`python3 infra/verify-planning.py` at the root. The latter builds unique API/web/crop
images, exercises authenticated real CPU inference on explicitly synthetic inputs,
full mobile/desktop confirmation and replan, PostgreSQL/Redis tests, restart,
fresh-database restore and isolated migration downgrade/upgrade. It requires Docker,
local package test environments and Playwright Chromium. Use a unique
`PLANNING_VERIFY_OUTPUT` directory per concurrent run. Inspect empirical status
separately; no synthetic suite establishes regional yield accuracy.
