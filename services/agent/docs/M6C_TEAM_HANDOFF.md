# M6C — Team integration handoff (members 1, 2, 4, 5)

Contracts: M0 (`contracts/models.py`, host ↔ agent) and integration **v0.1, PROPOSED** (`contracts/integration.py`,
agent/host ↔ geo/simulation). Schemas: `contracts/schema/*.json`; TypeScript: `contracts/ts/*.d.ts`. Every example
below comes from these models or the demo fixtures. Nothing here has been verified against a real teammate
service: none exists yet. Deeper references: `docs/M5_TEAM_INTEGRATION_CONTRACT.md` (full rules),
`docs/M6B_TEAM_ONBOARDING.md` (signing details).

## 0. Shared security boundary

```text
Browser (widget) ──session──▶ Host backend (BFF) ──API key + signed request──▶ AI service ──service token──▶ Geo / Simulation
```

| Host backend responsibility | What it means here |
|---|---|
| Authentication | Log the farmer in. The agent has no user accounts and never reads identity from headers. |
| Field authorization | Decide that *this farmer* may use *this field* (OWASP API1). A `field_id`, `result_id` or `session_id` is a reference, never permission. |
| Request signing | `X-FieldShift-Context-Attestation` over principal, field, revision, exact body bytes, ≤ 300 s expiry, random `jti`. Sign **every attempt** (retries too): the agent accepts each token once. |
| Trusted data acquisition | Build `FieldContext` facts from your store and the geo service. **Never sign values the browser sent** as if they were observations. |
| Secret protection | `AGENT_API_KEY`, `AGENT_CONTEXT_SIGNING_KEY`, service tokens: server-side only, never in the bundle. |
| Rate limiting | Per farmer and per IP, in front of the agent (not implemented in the agent). |

The HMAC proves *the host vouched for this request*. It does not prove a value is scientifically authentic.
`frontend/dev/referenceBff.ts` is a **dev-only reference**: it signs and replaces browser field data with the host
copy, but has no login, no field-access decision and no rate limiting. It is not an authentication solution.

## 1. Geo engineer (member 1)

| Item | Requirement |
|---|---|
| Operations | `GET /v1/capabilities` (`geo.field_profile: true`), `POST /v1/field-profile` `{contract_version, request_id, field_id}`; later `POST /v1/timeseries` |
| Field identity | Answer only for the requested `field_id`; echo it; `centroid` `[lon, lat]` WGS84 within about 2 km of the host's; unknown → `404 not_found`, not allowed → `403 field_not_authorized` |
| Provenance (per value) | `kind` (`satellite_product` for published EO/reanalysis products as distributed, `ml_estimate`, `deterministic_calc`, `reference`), `data_status` (`live`/`cached`/`fixture`/`unavailable`), `source` {provider, product_id, version}, `spatial.resolution_m`, time range, `retrieved_at`. SMAP L4 = `SPL4SMGP`, ≥ 9 km; NASA POWER ≥ 0.5°; SoilGrids is ISRIC, not NASA; downscaled values are a separate `ml_estimate` |
| Units (UCUM) | soil moisture `m3/m3`, precipitation `mm`, temperature `Cel`, pH `[pH]`, NDVI `1`, ET0 `mm` or `mm/d`, GDD `Cel.d` |
| Layer ids in the demo | `l_hls_ndvi`, `l_smap_rootzone`, `l_sm_30m` (ML estimate), `l_nasadem_slope` |
| Metric / chart ids | `c_water_season` → `m_sm_rootzone`, `m_precip`; `c_vegetation` → `m_ndvi` |
| Capabilities | `service: {service: "geo", implementation, mode, contract_version: "0.1"}`; only `mode: "live"` counts as real; stub/fixture values must say `data_status: "fixture"` |
| Missing data | `status: "partial"` and the fact with `data_status: "unavailable"` and **no value** |

```bash
uv run python -m agent_service.integrations.contract_check --geo https://YOUR-GEO/geo --token-env GEO_TOKEN --partial-field FIELD_WITH_GAPS --json geo-report.json
```

## 2. Simulation engineer (member 2)

| Item | Requirement |
|---|---|
| Scenario ids | `typical`, `drought_2024`, `heat_plus_2_5c` (M0 placeholders, yours to confirm). Report only those you really compute in `capabilities.simulation.scenarios`. |
| Scenario integrity (M6C) | `scenario` in the response **must equal** the request's; a different one is refused. The agent never runs a scenario the farmer did not name; when it is missing it says so and offers yours as a separate choice. |
| Rotation ids | Your ids (demo: `rot_adaptive`, `rot_conventional`); `rotation_ids: []` means "your default set"; ≤ `max_rotations` |
| Request | `POST /v1/comparisons` `{contract_version, request_id, idempotency_key, comparison: {field_id, field_context_revision, constraints (confirmed only), scenario, rotation_ids}}` + header `Idempotency-Key` |
| Result envelope | `{contract_version, request_id, service, result_id, status: accepted\|running\|completed\|failed, field_id, field_context_revision, scenario, engine {name, version}, created_at, computed_at, expires_at, comparison, failure, warnings}` |
| Lifetime and revision | Echo `field_context_revision`; serve `GET /v1/comparisons/{result_id}` unchanged until `expires_at` (≥ `result_ttl_s`); afterwards 404/410 |
| Numerical provenance | Scores `water_risk %`, `soil_cover_days d`, `n_input kg/ha`, `yield_risk %`, each with provenance (`deterministic_calc` or `ml_estimate`, uncertainty for estimates, `model_not_validated` warning when true); synthetic values `data_status: "fixture"` |
| Idempotency | Same `Idempotency-Key` while valid → same `result_id`, no recomputation; keys are scoped per verified farmer by the agent; make de-duplication atomic (concurrent identical POSTs) |
| Capabilities | `simulation: {compare_rotations, scenarios, max_rotations, result_ttl_s, async_jobs}`; with `async_jobs: true` answer `accepted`/`running` and let the agent poll |

```bash
uv run python -m agent_service.integrations.contract_check --sim https://YOUR-SIM/sim --field YOUR-FIELD --context field_context.json --token-env SIM_TOKEN --json sim-report.json
```

## 3. Frontend and shared-context engineers (members 4, 5)

**Mount** (see `frontend/src/demo/App.tsx` for a working host):

```tsx
<FieldShiftAssistant
  transport={{ baseUrl: "/your-bff/agent" }}     // your backend route, never the agent directly
  fieldContext={ctx}                             // FieldContext (M0) of the selected field, or null
  fieldName="Poltava north" locale="uk"
  capabilities={{ actions: ["map.set_layers", "panel.open", "compare.set_constraints"], simulation: true }}
  onAction={handleAction}
/>
```

| Topic | What to do |
|---|---|
| `FieldContext` props | `field_id`, `revision`, geometry/centroid, `facts` (from your store/geo), `available_layers`, `available_charts`, `constraints` (with status), `ui_state` |
| Revision management | Bump `revision` on **every** change (layer toggle, confirmed constraint, new data). The widget accepts a message's actions only at its revision plus revisions produced by that message's own actions. |
| `onAction` | `const problem = actionProblem(envelope, liveCtx, capabilities, fieldId, knownResults)`; if `problem` → `{status: "rejected", reason: problem}`; else apply and return `{status: "applied", revision: newRevision}` (`frontend/src/demo/hostActions.ts`). |
| Field authorization | In your backend, before signing. Never in the widget. |
| Comparison results | On `panel.open {panel: "comparison", ref_id, scenario}`: fetch `GET /v1/comparisons/{ref_id}` **through your backend**, check `field_id`, `status: completed`, `expires_at`, `field_context_revision ≤ current` and **`scenario` equal to the action's** (put it in `KnownResult.scenario`; `actionProblem` refuses a mismatch). Show the scenario and "synthetic" when `service.mode != "live"` (`frontend/src/demo/services.ts`, `ComparisonCard.tsx`). |
| Rejected and stale actions | The widget shows the reason ("view changed after the answer"); do not retry automatically. Constraint proposals are saved only when the farmer clicks Confirm. |
| Clarifying questions | A message may end with a question whose options are reply buttons (e.g. choosing another scenario). The click sends the option text as the next message. |
| Layout and map controls | The widget is a floating panel (`placement`, `theme`, `container` props); it never touches your map: every change goes through `onAction`. Keep your existing controls; the farmer's own changes bump the revision. |

## 4. Run the whole demo locally (signed path)

```bash
uv run python -m integration_stub --port 8100
```

```bash
AGENT_AGENT=scripted-llm AGENT_PORT=8002 AGENT_API_KEY=$K1 AGENT_CONTEXT_SIGNING_KEY=$K2 AGENT_REQUIRE_CONTEXT_ATTESTATION=true AGENT_GEO_PROVIDER=http AGENT_GEO_URL=http://127.0.0.1:8100/geo AGENT_SIM_PROVIDER=http AGENT_SIM_URL=http://127.0.0.1:8100/sim uv run python -m agent_service
```

```bash
cd frontend && FS_BFF_AGENT_KEY=$K1 FS_BFF_SIGNING_KEY=$K2 npm run dev
```

Open `http://localhost:5173/?host=full&backend=signed`. `K1`/`K2`: any two different random strings of ≥ 32 characters.
Acceptance: `uv run python -m evals.acceptance` (server half) and `FS_E2E_LIVE=1 npx playwright test e2e/live.spec.ts`
(browser half, needs :8000, :8001, :8002 and the stub). Report failures with the checker `--json`, your
`X-Request-ID`s, the agent's `/health/ready` and its `integration service=` log lines.
