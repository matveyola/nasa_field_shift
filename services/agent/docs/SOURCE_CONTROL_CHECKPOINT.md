# Source-control checkpoint and backup

**Current state (updated in M6A, 2026-11-06):** `master` has two commits: `0388e56` (M0–M5 baseline,
tag `m5-checkpoint`) and `6e36d64` (`.gitattributes`, LF endings). No remote is configured; nothing has been
pushed. The sections below describe the baseline as it was planned and committed.

## Offline backup (verified)

```bash
uv run python scripts/backup.py --dest D:/field-shift-backups
```

It writes `field-shift-agent-<HEAD>.bundle` with all branches and tags, runs `git bundle verify`, checks
every ref is in the bundle, restores it into a temporary directory and compares HEAD, then prints the
SHA-256. It refuses to overwrite and refuses a destination inside the repository. Only committed work is
included: `.env`, `data/`, model weights and `node_modules` are never in a bundle (they are untracked).

Executed on 2026-11-06: `D:\field-shift-backups\field-shift-agent-6e36d64.bundle`, 2,328,194 bytes,
SHA-256 `c9d43fa607bdbd276c3943dce7d262f629acd4ad1fde0ae65030215debc5dcbc`, refs `master 6e36d64`,
`m5-checkpoint 0388e56`, test restore OK. Keep a second copy on private storage you control.

Restore into a separate directory:

```bash
git clone D:/field-shift-backups/field-shift-agent-6e36d64.bundle field-shift-agent-restored
```

```bash
git -C field-shift-agent-restored tag
```

## Baseline as planned before the first commit

## What a checkpoint would contain

`git add --dry-run .` (stages nothing) lists **166 files, about 4.0 MB**:

| Area | Files |
|---|---|
| Contracts (M0 + proposed integration v0.1) | `contracts/` (models, schemas, TS types, fixtures), `CONTRACTS.md` |
| Agent service (M1–M5) | `agent_service/` incl. `integrations/`, `knowledge/` code |
| Contract stub | `integration_stub/` |
| Tests and evals | `tests/`, `evals/` (incl. `evals/reports/*`: generated, small, no FAO text) |
| Widget and demo (M4–M5) | `frontend/` without `node_modules`, `dist`, `dist-lib`, `test-results` |
| Docs | `README.md`, `docs/`, `frontend/README.md`, `frontend/docs/screenshots/` (12 PNG, ~2 MB) |
| Tooling | `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `tsconfig.json`, `.env.example`, `.gitignore` |

Largest files: `frontend/src/widget/contracts/validators.js` (generated, 0.5 MB), `uv.lock` (0.35 MB),
screenshots (~0.15–0.25 MB each), `evals/reports/retrieval-latest.json` (0.18 MB).

## What stays out (already in `.gitignore`, verified by the dry run)

| Excluded | Why |
|---|---|
| `data/` (FAO-56 HTML pages, `knowledge.sqlite` index) | FAO terms: no full copies in the repo; rebuildable (README › Knowledge base) |
| model weights (`D:\field-shift-models\hf`, outside the repo) | 2 × ~2.3 GB; pinned revisions are in the README |
| `.env` | secrets (none exists now) |
| `.venv/`, `node_modules/`, `frontend/node_modules/`, `__pycache__/`, `.pytest_cache/` | rebuildable |
| `frontend/dist/`, `frontend/dist-lib/`, `frontend/test-results/`, `playwright-report/` | build and test output |
| `.omc/` | local tool state |

The e2e fixtures (`frontend/e2e/fixtures/*.sse`) contain two short FAO-56 quotes with attribution,
the same as any citation the service returns; no page copies.

Outside this repository and therefore not covered: `../docs/TASK-01_Agent_Audit_and_Plan.md` and
`../.claude/launch.json`. Copy the TASK-01 audit into `docs/` if it should be versioned.

## Recommended steps (for the owner to run)

Review, then check the identity git will record:

```bash
git status --short
```

```bash
git add --dry-run .
```

```bash
git config user.name && git config user.email
```

Commit everything as one baseline (history before this point cannot be split retroactively):

```bash
git add .
```

```bash
git commit -m "Field Shift agent subsystem: M0 contracts to M5 integration layer (baseline)"
```

```bash
git tag m5-checkpoint
```

An offline backup of the full history that survives a lost laptop:

```bash
git bundle create ../field-shift-agent-m5.bundle --all
```

Before any push to a shared remote: make sure `.env` does not exist or is ignored, re-run
`git add --dry-run .`, and decide whether `frontend/docs/screenshots/` belongs in the main repo.

## Rebuilding what is not committed

```bash
uv sync --group rag && npm install && npm --prefix frontend install
```

```bash
uv run python -m agent_service.knowledge fetch
```

```bash
uv run python -m agent_service.knowledge build --no-embed
```

(With embeddings: download the pinned models first, see README › Knowledge base.)
