# M6 team handoff — connecting your service to the Field Shift agent

From member 3 (AI agent). Status on 2026-11-06: the agent side is ready for review and testing against
**your** services. No teammate service has been connected yet; everything below was verified only with
local fixtures and a contract stub. The contract is a **proposal (v0.1)** until you agree to it. Full
spec: [M5_TEAM_INTEGRATION_CONTRACT.md](M5_TEAM_INTEGRATION_CONTRACT.md).

## 1. What the agent subsystem already provides

- Agent service (`/v1/agent/stream`, `/v1/agent/message`, `/health/ready`) with tools: field facts, farmer
  conditions, UI action proposals, FAO-56 search with verified citations, and `compare_rotations`.
- Adapters for your services (fixture / off / HTTP), a validation boundary for everything you send, and
  readiness that reports your service separately (`integrations.geo`, `integrations.simulation`).
- The React widget (`frontend/src/widget`) with an action policy for hosts (`actionProblem`).
- A contract stub (`python -m integration_stub`) and a **contract checker** that tests your URL.

## 2. Member 1 (geo) must expose

| Endpoint | Purpose |
|---|---|
| `GET /v1/capabilities` | `service.service: "geo"`, `service.mode: "live"`, `geo.field_profile: true`, dataset list |
| `POST /v1/field-profile` | M0 `FieldProfile` for one field, plus the field's `geometry` and `centroid` (`[lon, lat]`) |

Every value: full provenance (`provider`, `product_id`, `version` or `"unverified"`, `spatial.resolution_m`,
`temporal`, `retrieved_at` UTC). SMAP L4 at ≥ 9 km and as `satellite_product`; 30 m downscaled moisture
as `ml_estimate` with `model` (+ `validation` if you have it); NASA POWER at ≥ 50 km; SoilGrids under
ISRIC, not NASA. Unavailable datasets: a fact with `data_status: "unavailable"` and no value.

## 3. Member 2 (simulation) must expose

| Endpoint | Purpose |
|---|---|
| `GET /v1/capabilities` | `service.service: "simulation"`, `mode: "live"`, `compare_rotations: true`, `scenarios`, `result_ttl_s`, `async_jobs` |
| `POST /v1/comparisons` | compute one M0 `RotationComparison`; echo `request_id`, `field_id`, `field_context_revision`, `scenario`; return `result_id`, `engine {name, version}`, `created_at`, `computed_at`, `expires_at` |
| `GET /v1/comparisons/{result_id}` | the same result until `expires_at`; `404`/`410` afterwards |

Honour `Idempotency-Key` (same key → same result, no recomputation). Units: `water_risk %`,
`soil_cover_days d`, `n_input kg/ha`, `yield_risk %`. ML scores carry `model` and, when known, `validation`.

## 4. Members 4 and 5 (host app) must implement

1. Own `FieldContext`; bump `revision` on every change; call the agent through **your backend** (BFF),
   which authenticates the farmer, checks field access, adds `AGENT_API_KEY` and **signs the request**
   (`X-FieldShift-Context-Attestation`, M6B: `docs/M6B_TEAM_ONBOARDING.md` §2, with a tested Node signer).
2. Mount `<FieldShiftAssistant>`; implement `onAction` with
   `actionProblem(envelope, liveCtx, capabilities, fieldId, knownResults)`; return
   `{status: "applied", revision}`.
3. For `panel.open {panel: "comparison", ref_id}`: fetch `GET /v1/comparisons/{ref_id}` through your
   backend, check `field_id`, `status`, `expires_at` and `field_context_revision ≤ current revision`
   (`KnownResult.field_context_revision`), then show it. A scenario selection is never a simulation run.
4. Fill `FieldContext.facts` only from the geo service (or let the agent verify them, see §6).

## 5. How to validate your API

```bash
uv run python -m agent_service.integrations.contract_check --geo https://YOUR-GEO/geo --token-env GEO_TOKEN --json geo-report.json
```

```bash
uv run python -m agent_service.integrations.contract_check --sim https://YOUR-SIM/sim --field YOUR-FIELD --context field_context.json --token-env SIM_TOKEN --json sim-report.json
```

The report says what it checked (`actual external service (mode live)` vs `contract stub` vs
`verification not performed`) and, per failure: operation, field path, expected shape, category and
what to change. It never prints the token or response bodies. Reference run against the stub:
`uv run python -m integration_stub --port 8100`, then the same command with `http://127.0.0.1:8100/geo|sim`.

Sample requests, generated from the contract models (`contract_check --examples`):

```json
POST {geo}/v1/field-profile
{ "contract_version": "0.1", "request_id": "req_example.geo", "field_id": "demo-poltava-01" }
```

```json
POST {sim}/v1/comparisons        (headers: X-FieldShift-Contract: 0.1, X-Request-ID, Idempotency-Key, Authorization)
{ "contract_version": "0.1", "request_id": "req_example.sim1", "idempotency_key": "idem_473d16806647256f50e14c9b9495352b",
  "comparison": { "field_id": "demo-poltava-01", "field_context_revision": 3,
    "constraints": [{ "id": "k_irrigation", "status": "confirmed", "source": "farmer_text", "evidence": "Поливу немає",
                      "type": "irrigation_available", "value": false }],
    "scenario": "typical", "rotation_ids": [] } }
```

## 6. Security assumptions you must keep

- **A `field_id` is not permission.** The agent has no user accounts. Your BFF authorizes the farmer for
  the field; your services must also refuse fields the caller may not access (`403 field_not_authorized`).
- **Request field data is an assertion.** The agent labels `live`/`cached` values in `FieldContext` as
  "supplied by the app, not confirmed by a data service" and never lets them back a "live data" claim,
  unless (a) the configured geo service returns the same fact in that turn, or (b) the BFF signed this
  exact request (M6B attestation: principal, field, revision, body hash, expiry). The old global flag
  `AGENT_CONTEXT_ATTESTED` is removed: it let any API-key holder pass forged "live" values.
- **Only `mode: "live"` is real.** Stubs/fixtures must label every value `fixture`. In production the agent
  refuses any service reporting `stub`/`fixture`, and refuses `http://` URLs.
- No redirects, JSON only, ≤ 2 MB responses; the agent never retries a comparison POST by itself.
- Service tokens stay server-side (agent `.env`, your config). Never in the browser.

## 7. Verified locally (fixtures / stub, not your services)

Profile ingestion and boundary rules; synthetic comparison explained with cite-only values and opened by
result id; unknown, expired, foreign-field, wrong-revision and wrong-scenario results refused (agent and
host); forged live values labelled and blocked; stale capability → tool error, never a fabricated result;
cancellation reaching the service call; field switch mid-answer; offline and timeout handling; demo/live
mislabelling refused. Commands: `uv run python scripts/verify.py` (everything, no paid calls).

## 8. Needs verification with your real services

Latency of real profiles and comparisons (agent deadlines: geo 8 s, simulation 30 s, both configurable);
async jobs (`async_jobs: true`) end to end; real authorization (403 paths); real dataset ids, label keys,
scenario ids; result retention; behaviour under your error responses; the live Claude run (needs a key).

## 9. Open decisions (need your agreement)

Endpoints and envelopes; scenario ids; score set and units; sync vs async; result TTL and storage;
how field authorization reaches your services (signed token vs network trust); whether the agent calls
geo directly or only reads the host's context; dataset id / label key catalogue; centroid tolerance
(0.02°); general questions without a field (proposal in the M5 doc §12). Details: M5 doc §11.

## 10. Reporting an integration failure

Send member 3: the checker's `--json` report (no secrets in it), the service's `X-Request-ID` values,
the agent's `/health/ready` output, and the agent log lines starting `integration service=` for those
request ids (they contain ids, statuses and timings, never payloads or farmer text). Say which build and
contract version your service runs.
