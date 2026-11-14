# M5 — Team integration contract (geo · simulation · host)

**Status: PROPOSAL, v0.1.** Written by member 3 (AI agent). None of it has been reviewed or agreed by
members 1, 2, 4 or 5 yet. Nothing below is a teammate's real endpoint: the URLs and payloads are what
the agent side implements and tests against a **contract stub** (`integration_stub/`).

M0 (`CONTRACTS.md`, `contracts/models.py`) is unchanged. The service envelopes below wrap M0 payloads
and live in `contracts/integration.py` → `contracts/schema/fieldshift-integration.schema.json`
(+ `contracts/ts/integration.d.ts`).

## M6B changes (still v0.1; host → agent only)

- `AGENT_CONTEXT_ATTESTED` is **removed** (it trusted every API-key holder). The host backend now signs each
  request: header `X-FieldShift-Context-Attestation` (principal, field, revision, body SHA-256, expiry), key
  `AGENT_CONTEXT_SIGNING_KEY`. Production requires it. See `docs/M6B_TEAM_ONBOARDING.md`.
- Idempotency keys are scoped to the **verified principal**; without an attestation the session id is only a
  de-duplication hint.
- The contract checker reports PASS / FAIL / SKIPPED / BLOCKED_NO_CREDENTIALS / BLOCKED_NO_SERVICE /
  NOT_PERFORMED and a separate real-integration verdict; it checks authentication (with `--token-env`), partial
  profiles (`--partial-field`) and unsupported scenarios.

## M6A changes (still v0.1, all additive or agent-side)

- **Trust boundary:** `FieldContext` facts are client assertions. `live`/`cached` values are labelled
  "supplied by the app, not confirmed by a data service" and never back a "live data" claim unless the geo
  service returns the same fact in that turn, or (M6B) the host backend signed the request.
  When the geo service and the request disagree, **the service's value is used** (M5 kept the app's).
- **Service identity:** a response whose `service.service` is not the called service is rejected.
- **Production:** a service reporting `mode` `stub`/`fixture` is treated as not connected.
- **Idempotency keys** are scoped to the caller's session: same session + same request → same key.
- **Hosts** also check `KnownResult.field_context_revision ≤ current revision`.
- **Capabilities** are re-probed after an `unavailable`/`timeout` failure instead of waiting 30 s.
- Contract checker: per-failure field paths, expectations, categories and fixes; `--json`, `--examples`.

## 1. Who owns what

| Member | Owns | Provides to the agent / host |
|---|---|---|
| 1 — geo | NASA / EO acquisition, field boundaries, soil, climate, vegetation, terrain, provenance | `GET /v1/capabilities`, `POST /v1/field-profile` (later `POST /v1/timeseries`) |
| 2 — simulation / ML | FAO-56 engine, crop parameters, water balance, rotation comparison, scenarios, ML predictions and uncertainty | `GET /v1/capabilities`, `POST /v1/comparisons`, `GET /v1/comparisons/{result_id}` |
| 4, 5 — frontend / shared context | map, charts, comparison view, field selection, `FieldContext` and its `revision`, applying UI actions | mounts `<FieldShiftAssistant>`, implements `onAction` with `actionProblem`, fetches profiles and results through the app backend |
| 3 — agent | LLM loop, RAG, tools, grounding, provenance checks, action proposals, widget | calls 1 and 2 through adapters; never computes geo or simulation values itself |

## 2. Compatibility matrix

| Boundary | Contract | Agent side | Verified against | Teammate side | Status |
|---|---|---|---|---|---|
| Host → agent (`AgentRequest` / SSE) | M0 v0.1 | implemented (M1–M4) | real agent service, widget, Playwright | demo host only | **Implemented and verified**; real host app pending (4, 5) |
| Agent → geo: capabilities, field profile | integration v0.1 (proposed) | adapters `fixture` / `http` / `off`, boundary checks, `get_field_facts` merge | in-process fixtures; contract stub over ASGI and over a real TCP socket | not started / unknown | **Protocol verified with the stub. Real service: PENDING (1)** |
| Agent → simulation: capabilities, compare, get result | integration v0.1 (proposed) | adapters, `compare_rotations` tool, result references | same as above | not started / unknown | **Protocol verified with the stub. Real service: PENDING (2)** |
| Host → geo / simulation (profile, result by id) | integration v0.1 (proposed) | demo host does it via `/integration` proxy | contract stub, Playwright live | real host + its backend | **Demonstrated in the demo. Real host: PENDING (4, 5)** |
| Host action execution | M0 `UIActionEnvelope` + widget `actionProblem` | widget policy, revision chain | unit, integration, Playwright (mocked and live) | real map / comparison modules | **Verified in the demo. Real host: PENDING (4, 5)** |
| Agent → geo timeseries (`get_timeseries`) | M0 `TimeseriesQuery` / `Timeseries` | not implemented | — | — | **Not started** |
| Trust boundary for request field data | agent-side policy (M6A) + signed per-request attestation (M6B) | labels, claim blocking, service-wins, HMAC verification, principal isolation | adversarial tests, evals, acceptance scenario (reference signer) | BFF must authenticate, authorize the field, build `FieldContext` and sign | **Agent side verified locally**; real BFF signing: PENDING (4, 5) |
| Live Claude provider | Anthropic SDK | implemented (M2); 6-case smoke suite (M6A) | mocked HTTP only | — | **No live call made (no credentials)** |

## 3. What the agent needs, and what it never does

- From **geo**: per field, the M0 `FieldProfile` (facts, layers, timeseries index) with full provenance,
  plus the service's geometry and centroid for that field, so the agent can check it is the same field.
- From **simulation**: an M0 `RotationComparison` for one field, revision and scenario, wrapped with a
  `result_id`, status, engine version, timestamps and expiry.
- The agent **never**: computes a geo value, a water balance, a score or a ranking; converts units;
  upgrades `fixture` to `live`; calls a URL that is not in its configuration; lets the model choose a
  host, a path, a field id or a credential.

## 4. Common rules (both services)

| Rule | Detail |
|---|---|
| Transport | HTTPS in production (the agent refuses `http://` there), JSON, UTF-8. No redirects: a 3xx is treated as a rejection. |
| Version | Request header `X-FieldShift-Contract: 0.1`, and `contract_version: "0.1"` in every body. Another version → `unsupported_contract`. |
| Correlation | `X-Request-ID` header and `request_id` in the body; echo `request_id` in the response. The agent derives it from the farmer's turn (`<turn request_id>.geo`, `….sim1`). |
| Identity | `service: {service, implementation, mode, contract_version}`. **`mode` is `live` only for a real service.** `stub` / `fixture` responses must label every value `data_status: "fixture"`; the agent rejects a stub that claims `live` or `cached`. |
| Auth (server-to-server) | `Authorization: Bearer <token>` from the agent's config (`AGENT_INTEGRATION_TOKEN`). Never sent to a browser. |
| Limits | The agent reads at most `AGENT_INTEGRATION_MAX_BYTES` (2 MB) per response; geo 8 s, simulation 30 s timeouts (configurable). |
| Errors | Non-2xx bodies are `IntegrationError` (below). |

`IntegrationError` (any non-2xx):

```json
{ "error": { "code": "not_found", "message_key": "error.not_found", "retryable": false, "request_id": "req_7f3a.sim1" } }
```

| `code` | HTTP | Agent treats it as |
|---|---|---|
| `invalid_request` | 400/422 | rejected (no retry) |
| `field_not_authorized` | 403 | rejected; the farmer sees "not available" |
| `not_found` | 404 | not_found (unknown field or result id) |
| `expired` | 410 | expired |
| `unsupported_scenario` | 422 | rejected |
| `unsupported_contract` | 400 | unsupported_contract |
| `unavailable`, `internal` | 503/500 | unavailable (retryable flag respected; the agent itself does not auto-retry) |

## 5. Capabilities — `GET /v1/capabilities`

A capability is an **offer**, never a completed computation. The agent offers `compare_rotations` to the
model only while the simulation service answers this with `compare_rotations: true` and at least one
scenario (cached 30 s). `/health/ready` reports it (`integrations.simulation.connected`, `service_mode`).

```json
{
  "service": { "service": "simulation", "implementation": "fieldshift-contract-stub 0.1", "mode": "stub", "contract_version": "0.1" },
  "simulation": { "compare_rotations": true, "scenarios": ["typical"], "max_rotations": 6, "result_ttl_s": 3600, "async_jobs": false }
}
```

```json
{
  "service": { "service": "geo", "implementation": "fieldshift-contract-stub 0.1", "mode": "stub", "contract_version": "0.1" },
  "geo": { "field_profile": true, "timeseries": false,
           "datasets": [ { "dataset_id": "l_hls_ndvi", "kind": "satellite_product", "available": true, "note": "synthetic fixture" } ] }
}
```

## 6. Geo — `POST /v1/field-profile` (member 1)

Request:

```json
{ "contract_version": "0.1", "request_id": "req_7f3a.geo", "field_id": "demo-poltava-01" }
```

Response (abbreviated: one fact, one layer, geometry cut with `…`):

```json
{
  "contract_version": "0.1", "request_id": "req_7f3a.geo",
  "service": { "service": "geo", "implementation": "fieldshift-contract-stub 0.1", "mode": "stub", "contract_version": "0.1" },
  "status": "complete", "generated_at": "2026-11-06T14:32:19Z",
  "geometry": { "type": "Polygon", "coordinates": [[[34.54, 49.6], [34.548, 49.6], "…"]] },
  "centroid": [34.544, 49.6025],
  "profile": {
    "field_id": "demo-poltava-01",
    "facts": [{
      "id": "f_sm_rootzone_2025_07", "domain": "water", "label_key": "fact.root_zone_soil_moisture",
      "quantity": { "value": 0.21, "unit": "m3/m3" }, "depth_cm": [0, 100],
      "provenance": {
        "kind": "satellite_product", "data_status": "fixture",
        "source": { "provider": "NASA NSIDC DAAC", "product_id": "SPL4SMGP", "version": "008", "level": "L4 (model-assimilated)" },
        "method": "Synthetic fixture value for development; not a real measurement.",
        "spatial": { "resolution_m": 9000.0, "note": "9 km grid; the radiometer footprint is about 33 km" },
        "temporal": { "start": "2025-07-01", "end": "2025-07-31", "aggregation": "monthly_mean" },
        "retrieved_at": "2026-11-06T09:00:00Z"
      }
    }],
    "timeseries_index": [],
    "available_layers": [{ "layer_id": "l_hls_ndvi", "label_key": "layer.hls_ndvi", "kind": "raster",
      "provenance": { "kind": "satellite_product", "data_status": "fixture",
        "source": { "provider": "NASA LP DAAC", "product_id": "HLSS30_VI", "version": "002" }, "retrieved_at": "2026-11-06T09:00:00Z" } }]
  },
  "warnings": [{ "code": "fixture_data", "detail": "synthetic fixture values, not measurements" }]
}
```

`status: "partial"` when some datasets are missing; an unavailable dataset is a fact with
`data_status: "unavailable"` and **no** `quantity`/`category` (a real `0` is a value, never "missing").

### Required provenance (checked by `agent_service/integrations/normalize.py`)

Whole response rejected when: `request_id` or `field_id` differ; the centroid is outside WGS84, is
`[lat, lon]` (rejected as a coordinate-order error) or more than ~2 km from the host's field
(`wrong_field`); a non-`live` service labels data `live`/`cached`; `generated_at` is in the future.

Single fact / layer dropped (reported to the model as `dropped`, never silently fixed) when:

| Dataset | Rule | Why |
|---|---|---|
| SMAP L4 (`SPL4SM*`) | `kind: satellite_product`; `spatial.resolution_m` ≥ 9000 | a 9 km model-assimilation product. A 30 m value is a separate `ml_estimate` with its own `model` (and `validation` when known) |
| NASA POWER | `spatial.resolution_m` ≥ 50 000 | 0.5° × 0.625° reanalysis-based grid, not an in-field sensor |
| SoilGrids | provider must not be NASA | ISRIC product. The agent has **no** runtime dependency on the SoilGrids API (see its service notice): geo should cache what it needs |
| Soil moisture / precipitation / temperature / pH / NDVI / ET₀ / GDD | unit must be `m3/m3` / `mm` / `Cel` / `[pH]` / `1` / `mm` or `mm/d` / `Cel.d` | no silent unit conversion |
| any | `retrieved_at` not in the future; `version: "unverified"` allowed but reported | |

Schema-level (M0): finite numbers only, UCUM unit allowlist, known `data_status`, `satellite_product`
and `reference` need `source.version`, `ml_estimate` needs `model`, numeric `llm_explanation` forbidden.

How the agent uses it: `get_field_facts` asks once per turn, adds facts the host's `FieldContext` does
not have, marks matching facts as verified, and uses the **service's** value when both disagree (reported
as a conflict, `low_confidence` warning; changed in M6A: the request is only an assertion). The host remains
the owner of `FieldContext`; the agent never writes back.

## 7. Simulation — `POST /v1/comparisons`, `GET /v1/comparisons/{result_id}` (member 2)

Request (send `Idempotency-Key: <idempotency_key>` too; same logical request → same key):

```json
{
  "contract_version": "0.1", "request_id": "req_7f3a.sim1", "idempotency_key": "idem_06fb709d19de59c4e272ee7e0d0029da",
  "comparison": {
    "field_id": "demo-poltava-01", "field_context_revision": 3,
    "constraints": [{ "id": "k_irrigation", "status": "confirmed", "source": "farmer_text", "evidence": "Поливу немає",
                      "type": "irrigation_available", "value": false }],
    "scenario": "typical", "rotation_ids": []
  }
}
```

Only **confirmed** constraints are sent; proposals the farmer has not confirmed never reach the simulation.

Response (abbreviated: one rotation, two scores):

```json
{
  "contract_version": "0.1", "request_id": "req_7f3a.sim1",
  "service": { "service": "simulation", "implementation": "fieldshift-contract-stub 0.1", "mode": "stub", "contract_version": "0.1" },
  "result_id": "cmp_5c79722b52984c39", "status": "completed",
  "field_id": "demo-poltava-01", "field_context_revision": 3, "scenario": "typical",
  "engine": { "name": "fixture-replay", "version": "m0-0.1" },
  "created_at": "2026-11-06T14:32:19Z", "computed_at": "2026-11-06T14:32:19Z", "expires_at": "2026-11-06T15:32:19Z",
  "comparison": {
    "scenario": "typical",
    "rotations": [{
      "rotation_id": "rot_adaptive", "rank": 1,
      "sequence": [{ "year_index": 1, "season": "winter", "crop_id": "winter_pea" }, { "year_index": 2, "season": "summer", "crop_id": "sorghum" }],
      "scores": {
        "water_risk": { "value": 22.0, "unit": "%", "provenance": { "kind": "deterministic_calc", "data_status": "fixture",
          "method": "Share of days with FAO-56 root-zone depletion above RAW; synthetic fixture value", "retrieved_at": "2026-11-06T09:00:00Z" } },
        "yield_risk": { "value": 15.0, "unit": "%", "uncertainty": { "kind": "ci95", "low": 9.0, "high": 23.0 },
          "provenance": { "kind": "ml_estimate", "data_status": "fixture", "model": { "name": "yield-risk", "version": "0.0-fixture" },
          "retrieved_at": "2026-11-06T09:00:00Z" } }
      }
    }],
    "constraint_weights_applied": { "irrigation_available": 1.0 }
  },
  "warnings": [{ "code": "fixture_data", "detail": "synthetic fixture result; no crop model ran" }]
}
```

(In the stub, `constraint_weights_applied` is copied from the static M0 fixture, not computed.)

| `status` | Meaning | Agent behaviour |
|---|---|---|
| `accepted` / `running` | job exists, no results | if `async_jobs: true`: polls `GET` (1 s) within its deadline; otherwise reports "still running". Never shows numbers |
| `completed` | `comparison` and `computed_at` present | validated, turned into cite-only facts |
| `failed` | `failure: {code, message_key}` | tells the farmer nothing was computed; no estimate |
| (no answer / error) | service unavailable, timeout, invalid | tool error; `source_unavailable` warning; no numbers |

Rejected results: a `service.service` other than `simulation`, other `field_id` (`wrong_field`), other `field_context_revision` (`wrong_revision`),
other scenario, `expires_at` in the past (`expired`), rotations that were not requested, score units other
than `%` (water_risk, yield_risk), `d` (soil_cover_days), `kg/ha` (n_input), a non-`live` service labelling
results `live`. ML scores without `model.validation` are kept and flagged "model not validated".

`GET /v1/comparisons/{result_id}` must return the same body (with the caller's `request_id`) until
`expires_at`, then `410 expired` or `404 not_found`. The **host** uses it to show the comparison the
agent refers to.

What the agent makes of it: every score becomes a fact `sim.<hash>.<rotation_id>.<metric>` with value,
unit, uncertainty and provenance kept verbatim; `provenance.method` gains
`| simulation output; scenario=…; result=…; engine=… …` so the context is never lost. The model can
only cite these as `{{fact:…}}` tokens; the server renders "**15% (9–23)** (simulation output, scenario
typical; ML estimate, not an observation; model not validated; demo data)". Prose that calls a computed
value "measured"/"observed"/"виміряно" is rejected (`estimate_as_observation`), and no rotation may be
called optimal or best.

## 8. Field identity, authorization and revisions

- **Identity is not permission.** A `field_id` in a request proves nothing. The agent service has no user
  accounts. In production the app's backend-for-frontend (BFF) must authenticate the farmer, check that
  they may access the field, and only then call the agent; geo and simulation must also enforce access
  for the field (a forwarded user assertion is a decision for 1, 2 and 5: see §11).
- The agent can only ask about **the request's own field**: the tools have no `field_id`, URL or host
  parameter (extra arguments are rejected), and every response is checked against that field.
- `FieldContext.revision` increases on every change (M0). Each simulation request carries the revision;
  a result for another revision is refused. UI actions carry the revision they were made for.
- **Revision chain (M4, unchanged):** the widget accepts an answer's actions at the answer's revision plus
  revisions produced by applying that answer's own actions (the host must return
  `{status: "applied", revision}`). Any other change makes the chips stale. Verified in the demo host's
  real state transitions (Playwright: map action then manual layer toggle → stale).
- **Field switch:** the widget aborts the running turn; late events are dropped; results and chips of
  field A can never apply to field B (widget checks field id; host checks again; the host also
  ignores a profile response that arrives after the farmer switched fields).

## 9. UI action responsibilities (host, members 4 and 5)

| Action | Who decides | Host must |
|---|---|---|
| `map.set_layers` (`auto` only for published products) | agent proposes, widget re-checks | check layer ids exist; never switch on a layer it cannot render |
| `panel.open {panel: "comparison", ref_id: result_id}` | agent proposes only for a result returned in that turn | `GET /v1/comparisons/{ref_id}` (via its backend), check `field_id`, `status: completed`, `expires_at`, then show; otherwise reject (`unknown_reference`, `expired`, `field_changed`) |
| `compare.select_rotations` | chip; agent proposes only ids from that turn's result | accept only ids in a live result it holds for this field |
| `compare.set_constraints`, `scenario.set` | always a farmer click | store; bump revision. A scenario selection is **not** a simulation run |

Use `actionProblem(envelope, ctx, capabilities, fieldId, results)` from the widget package with the
**live** store. Applying anything that changes `FieldContext` must bump `revision` and return it.

## 10. How to run the contract tests

```bash
uv run python -m integration_stub --port 8100
```

```bash
uv run python -m agent_service.integrations.contract_check --geo http://127.0.0.1:8100/geo --sim http://127.0.0.1:8100/sim
```

A teammate checks their own service the same way, with their URL (token from an env var, never inline):

```bash
GEO_SIM_TOKEN=... uv run python -m agent_service.integrations.contract_check --geo https://geo.example/geo --sim https://sim.example/sim --token-env GEO_SIM_TOKEN
```

```bash
uv run pytest tests/test_integrations.py
```

```bash
uv run python -m evals.run
```

The JSON Schema for any language: `contracts/schema/fieldshift-integration.schema.json`. The stub's
fault injection (`--fault sim:wrong_revision`, `geo:swapped_centroid`, …) reproduces each rejection.

## 11. Decisions that need agreement (open)

1. **Endpoints and envelopes above** (1, 2): paths, envelope fields, status values, error codes.
2. **Scenario ids** (2): `typical`, `drought_2024`, `heat_plus_2_5c` are M0 placeholders.
3. **Score set and units** (2): `water_risk %`, `soil_cover_days d`, `n_input kg/ha`, `yield_risk %`.
4. **Sync vs async** (2): is a comparison fast enough for one request (agent deadline 30 s), or `async_jobs`?
5. **Result retention** (2, 4, 5): TTL, and whether results are stored for the host to fetch by id.
6. **Authorization** (1, 2, 5): how the BFF's user decision reaches the services (signed field-access
   token vs. network trust). Until agreed, the agent sends only its service token.
7. **Profile vs FieldContext** (1, 5): the host merges the profile into `FieldContext`; does the agent also
   call geo directly (implemented, optional), or only read the host's context?
8. **Dataset ids and label keys** (1, 4): real `layer_id`, `metric_id`, `label_key` catalogue.
9. **Coordinate tolerance** (1): 0.02° centroid tolerance for "same field".
10. **General questions without a field** (all): see §12.

## 12. Proposal: general FAO questions without a selected field

Today M0 `AgentRequest.field_context` is **required**, so the widget says "select a field first".
Proposal (not implemented):

- M0 → **v0.2**: `field_context: FieldContext | null`. Loosening a required field is additive for senders
  but breaking for receivers that assume it, so it is a minor version with a coordinated rollout:
  agent first (accepts both), then widget (sends `null` when no field), then hosts.
- Agent with `null` context: only `search_knowledge` is offered; `get_field_facts`, `compare_rotations`,
  `propose_ui_actions` and `propose_constraints` are removed; no UI actions; grounding forbids field
  numbers entirely; the answer carries a "general guidance, no field selected" note.
- Widget: suggestions limited to FAO questions; history kept in a separate "no field" thread.
- Until agreed and versioned, nothing changes.

## 13. Implemented and verified vs. proposed

**Implemented and verified here** (tests in `tests/test_integrations.py`, eval category `integration`,
Playwright `e2e/integration.spec.ts` and `e2e/live.spec.ts`):
adapters (fixture, unavailable, HTTP), boundary checks, `compare_rotations`, result-reference checks
in agent and host, health reporting, cancellation into the external call, cross-field isolation, the
demo host flow (profile → comparison → panel by result id), the contract checker.

**Proposed, awaiting teammates:** everything in §4–§9 as an agreed contract; real geo and simulation
services; real host integration; authorization flow; `get_timeseries`.
