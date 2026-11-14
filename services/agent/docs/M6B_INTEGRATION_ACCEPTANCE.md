# M6B — Secure integration acceptance (Level A) and real-service status (Level B)

| Level | Scope | Status |
|---|---|---|
| **A** — local | trusted context, authorization boundary on the agent side, contract readiness, acceptance tests, onboarding | **Done, verified locally** (reference signer, contract stub, fixed-script model) |
| **B** — real | teammate geo / simulation services, real host backend and auth, real contract verification, real E2E | **PENDING**: no teammate endpoint, OpenAPI document or credential exists in this repository or its parent folder |
| Live Claude | one real model run | **PENDING**: no `ANTHROPIC_API_KEY`; the 6-case smoke suite (`evals.run --smoke`) is ready |

Nothing here is a real external end-to-end test. Where a teammate component is missing, the stand-in is named.

## 1. P0 finding and fix: context attestation

**Finding (M6A design).** `AGENT_CONTEXT_ATTESTED=true` was a global flag: every caller holding the API key had
its `FieldContext` facts treated as host-attested. A key holder could send a fabricated "live NASA SPL4SMGP"
value; it was shown without the "not confirmed" label and could back "live satellite data … right now". The
flag was bound to no user, field, revision or body.

**Fix (M6B).** The flag is removed (setting it stops startup). The host backend signs each request
(`agent_service/attestation.py`): HMAC-SHA256 over principal, field id, revision, SHA-256 of the exact body,
issue and expiry time (≤ 300 s). Trust reaches the agent only after verification (fail closed). Production
requires `AGENT_REQUIRE_CONTEXT_ATTESTATION=true` and a signing key that differs from the API key.

| Attack (prompt §P0) | Result | Test (`tests/test_attestation.py`) |
|---|---|---|
| 1 direct client sends fabricated live NASA data | labelled unverified, no live claim; 403 when attestation required | `test_1_*` |
| 2 valid API key + forged provenance | 403 when required; unverified otherwise | `test_2_and_4_*` |
| 3 correct field id with another field's values | body hash mismatch → 403 (also token for another field → 403) | `test_3_*`, `test_3b_*` |
| 4 authenticated transport carrying unverified context | API key is not attestation → unverified/403 | `test_2_and_4_*` |
| 5 trusted geo conflicts with client | service value wins even over attested context | `test_5_*` |
| 6 fixture service claims live | rejected at the boundary, no simulation values | `test_6_*` |
| 7 revision changes after verification | `revision_mismatch` → 403 | `test_7_*` |
| 8 client impersonates the backend via headers | `X-FieldShift-Principal`, `X-Forwarded-User`, `X-FieldShift-Trusted`, bogus token → 403 | `test_8_*` |
| forged / stale / over-long / malformed tokens | `bad_signature`, `expired`, `bad_lifetime`, `bad_principal`, `malformed`; token never echoed | `test_forged_*` |

The attestation proves *the host vouched for this request*. It does not prove scientific authenticity, and
it is only as good as the host's own authentication, which does not exist yet (Level B). The tests use the
reference signer as a controlled trusted-server fixture; they do not verify a real host authentication boundary.

## 2. Authorization model

Browser → authenticated host backend (BFF) → agent → trusted providers. A `field_id`, `result_id` or
`session_id` is a reference, never permission (OWASP API1, BOLA). The agent enforces: the request's own field
only (tools take no field id, URL or host), signed field + revision, result references valid only for results
returned in that turn for that field, and principal-scoped simulation de-duplication. The host must provide:
farmer authentication, the field access decision, `FieldContext` built from its store and the geo service,
signing, rate limiting, and result fetching through its backend (`docs/M6B_TEAM_ONBOARDING.md` §1).

## 3. Idempotency and session isolation

Key = SHA-256 of the comparison request, scoped to `principal:<id>` when attested, otherwise `session:<id>`
(a de-duplication hint only, never an isolation boundary).

| Case | Behaviour | Evidence |
|---|---|---|
| two users, identical requests | separate computations | test (`two_users_*`) |
| one user, two sessions; repeated request | one computation | test (`one_user_two_sessions_*`) |
| attacker reuses another session id | required mode: 403 (no attestation); dev mode: may share a result for an *identical* request (documented) | test (`an_attacker_reusing_a_session_id_*`) |
| field revision changes | new computation | test (`a_new_revision_*`) |
| expired result replayed | fixture/stub recompute; host refuses expired results; expired attestation → 403 | tests (M5, M6B), acceptance |
| retry after failure | the agent never retries a POST itself; a user retry re-sends the same key | by design (M5) |
| service restart | in-memory results lost → host gets 404 → chip refused | Playwright `resultGone` (M5) |
| concurrent identical requests | **not verified**: atomic de-duplication by key is the service's job | open |

## 4. Source identity

`/health/ready → integrations.<svc>.transport` keeps apart: configured endpoint (scheme + host),
transport-authenticated identity (TLS certificate vs nothing for `http`), the credential we present, and,
separately, what the service *claims* (`implementation`, `service_mode`). Per-value provenance is checked for
consistency, never treated as authentication. A test proves the adapter refuses a self-signed TLS server
that a client trusting the certificate accepts (`test_an_https_endpoint_with_an_untrusted_certificate_is_refused`).

## 5. Contract compatibility matrix (integration v0.1, PROPOSED)

| Endpoint / element | Agent support | Required | Agreed with owner | Breaking-change risk | Verified real |
|---|---|---|---|---|---|
| geo `GET /v1/capabilities` | yes | yes | no | low | **no** |
| geo `POST /v1/field-profile` | yes | yes | no | medium (dataset ids, label keys) | **no** |
| geo partial profile (`status: partial`, unavailable facts) | yes | optional | no | low | **no** |
| geo `POST /v1/timeseries` | no | optional (later) | no | — | **no** |
| sim `GET /v1/capabilities` | yes | yes | no | low | **no** |
| sim `POST /v1/comparisons` (sync) | yes | yes | no | high (scenario ids, score set, units) | **no** |
| sim async jobs (`accepted`/`running` + GET) | yes | optional | no | medium | **no** |
| sim `GET /v1/comparisons/{id}` | yes | yes | no | medium (retention/TTL) | **no** |
| `Idempotency-Key` | sent | yes | no | low | **no** |
| service bearer token | sent | yes | no | low | **no** |
| forwarded user/field authorization to services | not sent | not agreed | no | high | **no** |
| host → agent attestation header | verified | yes in production | no (host owners) | medium | **no** |

## 6. Contract checker (`agent_service.integrations.contract_check`)

Outcomes PASS / FAIL / SKIPPED / BLOCKED_NO_CREDENTIALS / BLOCKED_NO_SERVICE / NOT_PERFORMED, a separate
real-integration verdict, `--json`. New in M6B: authentication check (`--token-env`; the stub can require a
token via `STUB_TOKEN`), partial profile (`--partial-field`), unsupported scenario, revision binding, and
BLOCKED instead of a cascade of FAILs when a service does not answer. Run against the running stub:

```text
OUTCOMES: BLOCKED_NO_CREDENTIALS 2, PASS 11, SKIPPED 3
REAL INTEGRATION: NOT VERIFIED (geo, simulation is a stub, not a teammate service)
```

## 7. Canonical acceptance scenario (Poltava)

> **Superseded by M6C** (`docs/M6C_ACCEPTANCE.md`). The run below ran a `typical` comparison after the drought refusal
> without asking the farmer. M6C found that unsafe: the agent now never runs a scenario the farmer did not name and
> offers the alternative as an explicit choice. `evals/reports/m6b_acceptance.*` are kept as the record of that run.

Farmer: «У мене немає поливу. Покажи вологість ґрунту і порівняй дві сівозміни під час посухи.»

**Server half** — `uv run python -m evals.acceptance` → `evals/reports/m6b_acceptance.{md,json}`: real agent
app, signed requests (reference signer), LLM loop with a fixed script, HTTP adapters to the contract stub
(in process). **16/16 checks PASS**: turn completes; geo profile via the adapter (stub); the already confirmed
"no irrigation" constraint is not duplicated; drought refused from the service's declared scenarios and the
limitation stated; a typical-conditions comparison shown instead, labelled synthetic (`fixture`, warning);
no live claim; `map.set_layers` + `panel.open` chip bound to revision 3 and the result id; refused: unsigned
request, attestation for another field, wrong revision, expired attestation; dropped: invented comparison id,
Poltava result in a Mykolaiv turn; simulation unavailable and expired result → no values, no panel.

**Browser half** — `FS_E2E_LIVE=1 npx playwright test e2e/live.spec.ts` ("canonical acceptance"): real
widget, Vite proxy, agent on :8001 (scripted demo model, unsigned dev mode) over real HTTP to the stub on
:8100. Final state: revision «ред. 3», profile «отримано з гео-сервісу (stub, 8 показників)», answer states
that no comparison was computed (the stub offers only `typical`), no comparison panel, no invented numbers.
The demo model answers the rotation part only; the full multi-tool turn is the server half above.

## 8. What remains for Level B

Real geo and simulation endpoints with credentials → run §6 with `--token-env`, expect verdict `VERIFIED`;
real BFF signing and farmer auth → run the canonical scenario through the real host; agreement on the open
decisions in the M5 contract §11; concurrency de-duplication; a live Claude run (`evals.run --smoke`).
