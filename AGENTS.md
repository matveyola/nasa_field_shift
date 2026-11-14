# Field Shift

## Project boundaries

- The Python backend is a modular monolith in `apps/api`. Keep application rules in their owning domain and HTTP endpoints thin.
- The React application lives in `apps/web`. Maintain distinct Fields, Water, Agent, Rotations and Farm routes. Share selected field, season and scenario across routes.
- Web `src/features` owns user-facing behavior. Backend `modules` own application rules and their APIs. Keep shared UI and transport code in the web app's existing `shared` and `api` directories.
- Keep agent and model adapters replaceable. Identify mock data as such. Surface soil moisture and root-zone water reserve have separate units and data contracts.
- The API agent module owns chat, field context and evidence. The optional model inference process lives in `services/agent` with its own dependency lock and private API.
- Offline model training and evaluation live in `packages/field-shift-ml` with a separate dependency lock. Workers load versioned artifacts from their owning API modules under the existing release gates.
- PostgreSQL owns job state, attempts and results. Redis delivers job identifiers. Submit work durably before publishing, acknowledge after execution, and fence writes to the current attempt.
- When adding a worker task, document its execution bound, retry policy, idempotency and recovery behavior. Use separate queues for simulation and geospatial ML.

## Testing and CI policy

1. During implementation, run Ruff on the affected Python files and run directly affected tests. For frontend edits, use the affected tests and TypeScript checks where appropriate.
2. Run the fast suites at integration or review time. Run Docker builds and slower suites when their inputs change or when they verify an affected boundary.
3. Prefer deterministic tests at the smallest layer that verifies the behavior. Cover valid inputs, edge cases, failure paths and recovery where applicable.
4. Before opening a PR, run the fast unit suites and impacted integration tests. Report which checks were unexecuted and why.
5. Escalate verification for authentication, security, database schemas and migrations, public contracts, and critical data pipelines.
6. **Never fit a test to the implementation just to make it pass. Never delete, weaken, skip or bypass a failing test for a green result.** Find and fix the cause. Change an expectation only when the intended behavior changes or the existing expectation is demonstrably incorrect, and record the reason.
7. Diagnose the root cause of a CI failure before rerunning unrelated checks.

Keep temporary work notes outside the delivered source tree or remove them after verification.

## Development agent tree

- For multi-component implementation or explicitly delegated work, use [.agents/skills/agent-tree/SKILL.md](.agents/skills/agent-tree/SKILL.md) and spawn bounded subagents when available. Small edits can stay with one implementer and appropriate checks.
- Roles live in `.codex/agents`: `task_lead`, `implementer`, `researcher`, `verifier`, `test_runner`, and `integrator`. The root coordinates the whole tree; task leads may delegate within its shared capacity and ownership allocation.
- Each implementation leaf in delegated work needs independent verification and test evidence for the same frozen revision. An author cannot approve their own change. Missing mandatory checks block acceptance; later edits invalidate affected evidence.
- Use separate worktrees for concurrent writers. Freeze a checkout during review/testing. Assign one owner to shared files such as `docs/status.md`; worktrees do not isolate databases, Redis, ports or Docker resources.
- Respect the effective runtime slot limit. Use staged execution and agent reuse when capacity is limited; do not recursively spawn reviewers of reviewers. The workflow depth budget is three edges from the root.
- These are development roles, separate from the product agent in `services/agent`. Role instructions do not grant new permissions or publishing authority. Existing issue integration rules still apply.

## GitHub issue execution

- For issue implementation, read and follow [.agents/skills/issue-worktree/SKILL.md](.agents/skills/issue-worktree/SKILL.md).
- Establish the person's name before creating branches; ask only when it is not already provided. Use `<name>-jobs` (for Nazar, `nazar-jobs`), with no leading slash.
- Create each issue's branch and isolated worktree from that person's jobs branch. Reserve its checkout for integration. Never implement concurrent issues in the same working directory.
- The user authorizes merging verified issue work into the personal jobs branch and pushing that branch to `origin`. This supersedes older issue instructions to use `main` or stop at a PR. Do not merge into `main` or deploy without a separate request.
- Serialize integration, combined-tree checks and push using the skill's repository-wide lock. Preserve real commit metadata and never force-push shared history.
- Planning prerequisites remain: #2 first, then #3–#6 in parallel, then #7. Each prerequisite must be present in the jobs branch before dependent work begins.

## Delivery harness and commit policy

- Follow `docs/roadmap.md` and record completed checkpoints in `docs/status.md`.
- Write product documentation around behavior, interfaces, architecture and operating constraints. Commit messages describe actual additions and changes.
- Preserve established behavior, visual style, module boundaries, dependency locks and tests unless an approved requirement or verified issue requires a change.
- One coherent feature or component boundary per commit, with its related contracts, tests and dependencies. Keep manifest/lock pairs together. Avoid splitting a change into file-only commits that obscure its purpose. State explicitly when a checkpoint cannot run independently.
- Commit subjects describe the actual change. Bodies explain what changed, how, why, verification and known limitations. Use the configured identity; never add generated-by/co-author trailers or fabricate dates, authorship claims or work performed.
- Separate feature additions, documentation, infrastructure and verified fixes. Fix commits describe the reproduced issue, cause and verification. Never intentionally introduce defects or invent bug history. Recovery exercises must be labeled as exercises.
- Keep each checkpoint reviewable and reversible. Use `git revert` for shared history; do not rewrite published history or use destructive resets. Database rollback requires a tested restore to a fresh database, not merely a code revert.
- Do not publish, deploy or push without explicit authorization. Do not commit secrets, environments, dependency caches or generated runtime data.
- Update `docs/status.md` when readiness, verification or material limitations change. Keep it a concise status report rather than a transcript of individual file edits. Never claim an unrun check passed.
