---
name: agent-tree
description: Coordinate Field Shift development through bounded subagent trees with isolated implementation, independent verification, test execution and serialized integration. Use for delegated project work, multiple workstreams or an explicit agent-tree request.
---

# Field Shift development tree

This workflow orchestrates development agents, not the product assistant in `services/agent`.
Read repository `AGENTS.md`. For GitHub issues also use the existing [issue-worktree skill](../issue-worktree/SKILL.md); its branch, prerequisite, lock and publication rules remain authoritative.

## Shape and ownership

The root owns the user's scope, acceptance criteria, shared files, dependency graph and global slot ledger. Delegate independent workstreams to `task_lead`. A lead delegates bounded work to `implementer` and optionally `researcher`, then obtains `test_runner` and independent `verifier` evidence for every implementation leaf. Research and review leaves terminate; they do not each spawn another review tree.

For a small change, omit the lead and use direct children. For documentation/configuration changes, use relevant validation rather than unrelated application suites. For a multi-component task, explicitly spawn subagents when available; do not silently simulate independent review with the same author.

Use custom roles when the spawn tool supports role selection. If it only accepts a task/message, explicitly pass the instructions from the corresponding `.codex/agents/<role>.toml`, the skill path and the task packet. Merely naming a task `verifier` does not load its role. Never invent unsupported tool parameters. If subagents are unavailable, report that independent verification remains unavailable and perform useful local checks without claiming it happened.

Assign one writer per checkout. Concurrent implementations use separate worktrees, even within one issue; issue worktrees start from the personal jobs branch according to the issue skill. For non-issue work use the agreed base and preserve user changes. A component depending on another's unpublished changes waits for its prerequisite or is implemented sequentially in the owned checkout. Reviewers and test runners may share a frozen checkout. The root alone owns shared status documentation unless it delegates that ownership explicitly.

## Bounded scheduling

- The project requests up to eight concurrent subagent threads, excluding the root. Runtime/organization limits can be lower; use the effective limit reported by tools.
- The root allocates slots across the entire tree, including leads; children request slots before spawning. Default workflow depth is three edges from root, a coordination rule rather than a claimed runtime setting. Do not configure an undocumented depth key.
- With four total slots, use root → lead → one worker, keeping a slot for checks. Run researcher, implementer, test runner and verifier in stages. With more capacity, run independent workstreams concurrently while preserving capacity for a runnable leaf.
- Do not fill capacity with parents waiting for children. Finish/reuse existing agents before expanding. Release completed threads only if the client exposes that operation; otherwise reuse them via follow-up tasks. At the depth limit ask an ancestor to schedule remaining work as a sibling.
- Two unsuccessful fix/verification cycles on the same finding trigger a parent-level diagnosis and revised plan, not more recursive spawning or an automatic pass. Continue independent authorized work while resolving the concrete blocker.

## Task packet and evidence

For changes crossing contracts and consumers, establish the owning contract first. In this repository the agent schemas/types live in `services/agent/contracts`; validator generation/checks live in `services/agent/frontend`. Read the export/generation commands before changing the schema, update its derived artifacts in the implementation phase, then check freshness before debugging consumers. Do not invent a `packages/contracts` build or introduce pnpm into this npm/uv repository. Delegate the consumer phase with a minimal fresh task packet containing the settled contract, revision and acceptance criteria, rather than the entire exploration transcript. Preserve the repository's coherent-commit policy; a phase boundary does not require an incomplete file-only commit.

Every delegated task carries:

```text
Task ID / parent / role / depth / allocated slots:
Goal and acceptance criteria:
Checkout (absolute), branch, base SHA, target SHA if reviewing:
Owned paths; shared files and resource ownership:
Dependencies and available evidence:
Required checks and mode (affected / fast / full):
Allowed actions and inherited publication authorization:
Return: outcome, revision, findings, commands/results, logs, limitations.
```

Do not require a commit solely for review. For an uncommitted target, record HEAD, `git status --short`, and content hashes for all modified and untracked deliverable files (plus the deletion list). Freeze that state until checks finish. A HEAD SHA alone does not identify uncommitted changes. Keep the manifest and logs outside the source tree; do not include secrets or ignored runtime data. Recheck the state after verification.

Each implementation leaf moves through implementation → frozen handoff → test execution and independent review → acceptance or fixes. Review may start alongside tests on the same frozen state, but final acceptance consumes the test report. The implementer cannot verify its own change. Any subsequent source change invalidates affected evidence; rerun appropriate checks and obtain fresh review. Do not rerun unrelated suites without a reason.

Test execution follows [the test matrix](references/test-matrix.md). Use `python3 infra/verify.py --mode tooling` for this setup and `python3 infra/verify.py --mode fast --build` for the prepared application's deterministic local checks. The runner stops on failure; it does not install dependencies or replace integration/E2E verification. Required failures, stale evidence and unrun mandatory checks block acceptance. Reports distinguish PASS, FAIL and BLOCKED and enumerate optional omissions. Parents inspect evidence and reconcile disagreement using original requirements and reproductions; do not vote or choose the most optimistic report.

## Integration and completion

After leaf acceptance, verify cross-component interfaces and the combined tree. For authorized issue delivery, assign a single `integrator` and use the existing repository lock and integration helper; do not have every lead merge or push. Integrator checks cannot be replaced by earlier leaf reports. Outside issue delivery, do not infer publication authorization from the existence of these roles.

Return implemented behavior, reviewed/tested revision, actual checks, unresolved risks and delivery state. Only the assigned status owner updates `docs/status.md`. Keep task ledgers and transient logs outside the delivered tree. Configuration and instructions guide the model; they are not a deterministic enforcement service or a replacement for CI and the integration lock.
