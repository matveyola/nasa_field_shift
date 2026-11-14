# M6C — Demo correctness, scenario integrity and acceptance evidence

Status vocabulary, never merged: **verified locally** (our code, tests and fixtures) · **verified against the
contract stub** (`integration_stub`, synthetic, not a teammate service) · **verified against a teammate service** ·
**blocked: no service** · **blocked: no credentials** · **not tested**.

| Boundary | Verified locally | Contract stub | Teammate service | Notes |
|---|---|---|---|---|
| Agent API (SSE, signed requests) | yes | — | blocked: no service (real host backend) | reference signer in tests, dev reference BFF in the browser |
| Geo provider | yes | yes (HTTP, browser + acceptance) | blocked: no service | |
| Simulation provider | yes | yes (HTTP, browser + acceptance) | blocked: no service | |
| Host action adapter | yes (demo host, Playwright) | — | blocked: no service (real host app) | |
| Anthropic provider | mocked HTTP only | — | blocked: no credentials | `evals.run --smoke` ready; no live call made |
| FAO RAG | yes (lexical index, evals) | — | — | dense/rerank modes not run here |

Machine-readable: `evals/reports/m6c_acceptance.json` (`compatibility`, `turns`, `checks`).

## 1. Drought → typical substitution: root cause

The M6B report showed a drought request answered with a `typical` comparison. Traced:

| Candidate | Finding |
|---|---|
| Scripted model | **Origin.** The M6B acceptance script (`evals/acceptance.py`, round 2) called `compare_rotations("typical")` after the drought refusal, without asking the farmer. The demo model (`demo_script`) did not. |
| Tool selection / `compare_rotations` | **Enabler.** The tool accepted any scenario the model named; nothing tied it to what the farmer asked. A real LLM could do the same. |
| Capability negotiation | Correct: the stub offers only `typical`; `drought_2024` was refused locally from capabilities. |
| Fixture provider / stub | Correct: answers `failed` for scenarios it does not have; never invents numbers. |
| Fallback policy | None existed in code: the substitution was a model choice. |
| Provider response check | Correct: a response for another scenario was already refused (`normalize.py`). |
| Grounding / rendering | Partly protective: each value already rendered "сценарій typical", but prose could still call those values drought results. |
| Frontend | **Gap.** `panel.open` carried no scenario, so the host could not check that the fetched result was for the scenario the answer was about. |

## 2. Fixes and contract implications

- **Tool guard** (`tools.compare_rotations`): the scenarios named in the farmer's current message (`agent_service/scenarios.py`)
  are the only ones that may run. Another one → `scenario_not_requested`, nothing computed. A message naming no
  scenario constrains nothing.
- **Server-side offer** (`llm_agent._scenario_offers`): when a requested scenario is not offered, the message ends with a
  clarifying question whose options name the scenarios the service has («Порівняй сівозміни для сценарію: типові умови.»).
  Clicking one sends it as the next message, which names that scenario, so the tool accepts it: the farmer chose.
- **Grounding** (`grounding.check`, `scenario_mismatch`): a paragraph citing simulation values of scenario A may not name
  only scenario B ("during drought, risk is 22 % (scenario typical)" is removed). Naming both is allowed.
- **Contract (M0, additive):** `OpenPanelAction.scenario: ScenarioId | None`. The agent server fills it from the verified
  result (the model cannot set it); `ui_action_problem` refuses a mismatch; the widget's `actionProblem` refuses a fetched
  `KnownResult` whose `scenario` differs (fail closed when unknown). Schema, TypeScript and validators regenerated.
- **Stub fault `wrong_scenario`** for acceptance; **demo model** handles the multi-request message (one call per request)
  and never picks a scenario the farmer did not name.

Tests: `tests/test_scenario_integrity.py` (unavailable drought; explicit choice; supported drought keeps its name;
model substitution refused; typical values described as drought removed; honest dual naming kept; server refuses
mismatched panel), provider mismatch (`test_trust_boundary.py::test_simulation_identity_and_scenario_must_match`),
widget (`policy.test.ts` "another scenario"), acceptance checks 5–7, 10–13, 22 and 25.

## 3. Browser multi-tool trace (live, real services on this machine)

`FS_E2E_LIVE=1 npx playwright test e2e/live.spec.ts -g canonical`: widget → Vite → `/agent-bff` (dev reference host
backend, signs every request, replaces browser field data with the host copy) → agent :8002
(`AGENT_REQUIRE_CONTEXT_ATTESTATION=true`, scripted model) → contract stub :8100 over HTTP.

1. Direct POST to :8002 without the host backend → **401**.
2. Poltava selected, revision 3; canonical text sent.
3. Tools: `propose_constraints` (already confirmed → nothing proposed), `get_field_facts` (water: SMAP `SPL4SMGP`,
   GPM precipitation, ML 30 m estimate), `propose_ui_actions` (SMAP layer, auto), `compare_rotations(drought_2024)` → error.
4. Host applies the SMAP layer → revision 4. No comparison panel, no simulated numbers; the offer buttons appear
   (screenshot `frontend/docs/screenshots/m6c-1-drought-unavailable.png`).
5. Farmer clicks «Порівняй сівозміни для сценарію: типові умови.» → `compare_rotations(typical)` → synthetic values
   labelled "результат моделювання, сценарій typical … демо-дані" → chip → host fetches the result by id, checks field,
   revision, expiry and scenario → panel "Порівняння сівозмін · typical", computed for revision 4 (`m6c-2-typical-comparison.png`).
6. Switch to Mykolaiv: the Poltava panel disappears. Same text: «Без поливу» shown as *proposed* with Confirm/Reject;
   conditions and revision unchanged; "no moisture layer, map unchanged" (`m6c-3-constraint-proposed.png`).
7. Confirm → condition saved, revision 1 → 2. Final state: `{"field": "demo-mykolaiv-01", "revision": "ред. 2", "confirmedConditions": 2}`.

Duplicate `turn.completed` frames: the client returns on the first and stops reading (widget test
"a duplicated turn.completed frame never duplicates the message"). Mid-answer field switch: replay suite.

## 4. HMAC and replay review

| Property | Bound? | Evidence |
|---|---|---|
| Principal, field id, field revision, exact body bytes, expiry (≤ 300 s, 30 s skew) | yes | `test_attestation.py` (attacks 1–8, forged/stale) |
| API operation | not in the signature | the two signed endpoints (`/v1/agent/stream`, `/v1/agent/message`) run the same operation (one turn); with single use, a token cannot be used on both |
| Replay within the lifetime | **was possible: confirmed and fixed** | one signed request ran 3 turns (6 model calls), also on the other endpoint. Now each token is accepted once (`api._first_use`); replay → 403 `replayed`, no model call (`test_a_signed_request_is_single_use_on_every_endpoint`). Reference signers add a random `jti` so a re-signed retry of identical bytes differs. |
| Replay memory scope | one process | in-memory, ≤ 50 000 entries, purged after 360 s. Several workers or instances need a shared store or sticky routing (documented, not built: no Redis for a local demo). |
| Repeated computation | bounded | simulations are de-duplicated by principal-scoped idempotency key; agent turns by single-use tokens |
| Trusted caller | API key + signature | rate limiting is the host backend's job (not implemented in the agent) |
| Scientific authenticity | **not proven by HMAC** | the host must not sign browser-asserted values as observations; the reference BFF replaces them with its own copy |

## 5. Scientific provenance

`tests/test_provenance_regression.py`: one answer with a NASA-derived value (SMAP `SPL4SMGP v008`, provenance identical
to the context), an ML estimate ("комп’ютерна оцінка, не вимір; точність ще не перевірена"), synthetic simulation values
(`fixture`, "сценарій typical", `fixture_data` warning) and an FAO-56 citation. The sentence using the citation to vouch
for a field number (0,35 m³/m³) is removed (`general_source_as_field_data`, `number_not_in_source`); every remaining number
is a server-rendered fact. M3 citation checks unchanged.

## 6. Not done / blocked

Real geo, simulation and host services (Level B); real host authentication, field authorization and rate limiting;
a live Claude run (no credentials); concurrent identical comparison requests (service-side atomic de-duplication);
cross-instance replay memory.
