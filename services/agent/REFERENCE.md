# Field Shift agent service

This reference covers the agent service, standalone demo frontend, evaluation reports and helper tools. For the private service deployment and local launch commands, see [README.md](README.md).

The AI-agent backend for Field Shift (NASA Space Apps 2026). This repository also holds the shared integration contracts: see [CONTRACTS.md](CONTRACTS.md).

## Status (M5)

| Part | State |
|---|---|
| HTTP API, validation, SSE streaming, auth, CORS, limits, errors | **Implemented** and tested (M1) |
| LLM agent: provider layer, bounded tool loop, tools, grounding | **Implemented** and tested offline with a scripted model (M2) |
| Knowledge base: FAO-56 chapters 1, 5–8, hybrid retrieval, `search_knowledge` tool, verified citations | **Implemented** (M3). BM25, bge-m3 dense, RRF fusion and bge-reranker-v2-m3 all run locally on CPU; measured results are in **Knowledge base** |
| Assistant widget: React launcher + panel, SSE client, citations, condition chips, host action adapter, demo host | **Implemented** (M4) in [`frontend/`](frontend/README.md): unit, integration and Playwright tests against replayed and live backend responses |
| Gemini provider (`gemini-3.8-flash`), the primary real model | **Implemented** (Task 14); tested through the real `google-genai` SDK with a mocked HTTP transport. **First live answer on 2026-11-10**: one field question in the demo page (4 model calls, 2 tools, 25 s) after tool results were sent with role `user` (the live API rejects `tool`). Live smoke suite on 2026-11-10: 6/6 passed, about 0.08 USD estimated from published prices |
| Anthropic Claude provider (`claude-opus-5-5`) | Optional, non-default, never a fallback. Tested with a mocked HTTP transport only; no live call made |
| Field data | Comes only from the `field_context` in each request. Bundled fixtures are **synthetic** (`data_status: "fixture"`) |
| Geo and simulation integration: ports, fixture / unavailable / HTTP adapters, validation boundary, `compare_rotations` tool, result references, readiness | **Implemented** (M5) against a **proposed** contract v0.1 ([docs/M5_TEAM_INTEGRATION_CONTRACT.md](docs/M5_TEAM_INTEGRATION_CONTRACT.md)). Verified with in-process fixtures and the contract stub (`integration_stub/`) over HTTP. **No teammate service has been connected or tested** |
| Rotation simulation itself, image and voice input, live NASA data, other knowledge sources (SARE, regional guides) | **Not implemented by the agent** (simulation belongs to member 2). Without a connected simulation service the agent says so and is blocked from claiming results |

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run python -m agent_service
```

The service listens on `http://127.0.0.1:8000`; interactive docs are at `/docs`. The default agent is the deterministic M1 agent. Pick a mode with `AGENT_AGENT` (copy `.env.example` to `.env`):

| `AGENT_AGENT` | What runs | Key needed | Cost |
|---|---|---|---|
| `deterministic` (alias `deterministic-dev`) | M1 keyword rules | No | None |
| `scripted-llm` | The real LLM loop, tools and grounding, with a scripted fake model: reads facts, searches FAO-56, or asks the simulation service, and cites what comes back | No | None |
| `gemini` | **Primary real model:** Gemini 3.8 Flash through the official `google-genai` SDK | Yes (`GEMINI_API_KEY`) | Paid API usage (lower) |
| `anthropic` | Optional: Claude Opus 5.5 through the official `anthropic` SDK | Yes | Paid API usage (higher) |

```bash
uv run --env-file .env python -m agent_service
```

### Gemini 3.8 Flash: the primary real model

Gemini 3.8 Flash (`gemini-3.8-flash`) replaces Claude Opus 5.5 as the production chatbot model because it costs
less. The free scripted demo is unchanged, and so is every server-side check (tools, grounding, citations,
scenarios, constraints, UI actions).

1. **Get a key.** In Google AI Studio (<https://aistudio.google.com/apikey>), sign in and click **Create API key**
   (import an existing Google Cloud project first if you want the key there).
2. **Keep it server-side.** Put `GEMINI_API_KEY=...` in `.env` (git-ignored) or the server's secret store. The
   `google-genai` SDK reads it (`GOOGLE_API_KEY` wins if both are set); this service only checks that one is
   present, and never reads, logs or returns the value. Never put it in frontend code, `VITE_*` variables,
   screenshots or logs.
3. **Configure:** `AGENT_AGENT=gemini`, optionally `AGENT_GEMINI_MODEL=gemini-3.8-flash` (default) and
   `AGENT_GEMINI_THINKING_LEVEL=low` (default; `medium` for harder multi-tool turns; `high` only by choice;
   `minimal` is not supported by this model and is refused at startup).
4. **Start** with `uv run --env-file .env python -m agent_service`. `GET /health/ready` reports
   `"agent": "llm/gemini", "model": "gemini-3.8-flash"`, and the widget shows "AI model · gemini-3.8-flash". Each
   answer's `meta.model_id` is the model version Google reports for that call.
5. **Without a key** the service starts but is not ready: `/health/ready` answers 503
   `{"reason": "llm_credentials_missing"}` and turns are refused. It never falls back to Claude or to the scripted
   model. For a free demo, choose `AGENT_AGENT=scripted-llm` explicitly.
6. **Bounded live smoke test** (6 cases: Ukrainian field facts, a constraint proposal, the soil-moisture map
   action, FAO-56 with verified citations, the unsupported drought scenario, the synthetic rotation comparison;
   at most 4 model calls each, 2048 output tokens per call). It costs a few cents at most and exits with code 3
   without a key:

   ```bash
   uv run --env-file .env python -m evals.run --provider gemini --smoke
   ```

   It writes `AGENT_DATA_DIR/evals/reports/gemini-latest.md` / `.json` with the real model id, tools called, pass/fail, latency
   and the provider's input, output and thinking token counts per case, plus an estimated cost.

**Token usage and cost.** Every model call logs one `llm round` line and every turn one `turn usage` line with
the provider's input, output and thinking token counts (never the farmer's text). Set
`AGENT_LLM_PRICE_INPUT_USD_PER_MTOK` and `AGENT_LLM_PRICE_OUTPUT_USD_PER_MTOK` to add an `est_usd` estimate (thinking
is billed as output), and `AGENT_LLM_PRICE_CACHED_INPUT_USD_PER_MTOK` to price the input Gemini serves from its
implicit cache ($0.075 per million on 2026-11-12, a tenth of the input price; `cache_read` in the log). Without it
cached input is priced like any input, which roughly doubled the estimate in live tests. Prices change: on 2026-11-07 Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing)
listed Gemini 3.8 Flash at $0.75 input / $3.75 output per million tokens (paid tier, standard, through 2026-12-31)
and $1.50 / $7.50 from 2027-01-01. Check the page before relying on a number. Measured on 2026-11-12 (estimates
from provider-reported tokens at those intro prices, not a bill or a promise): the 6-case live smoke run cost about
1 cent per answer with Gemini 3.8 Flash (about 9 s each), and the 8 elements-board questions about 1.2 cents (2.9
model rounds per answer). Gemini 3.5 Flash-Lite passed the same 6 cases at about half the cost and twice the speed,
but its answers were worse on the board questions (an empty reply about the field, a garbled one about season rain,
and "I saved your conditions" for conditions that were only proposed), so 3.8 Flash stays the default. Google Search grounding is not enabled (it is billed
per query, and FAO-56 retrieval is the source workflow).

**How it is wired** (`agent_service/llm/gemini_provider.py`): `client.aio.models.generate_content` (stateless:
each call carries the whole turn), function declarations built from the same tool JSON Schemas (`const` written as
a one-value `enum`), `tool_config` mode `AUTO`, automatic function calling disabled, `thinking_config.thinking_level`,
`max_output_tokens` (which includes thinking on Gemini), and no `temperature` / `top_p` / `top_k` /
`candidate_count` (deprecated or unsupported for Gemini 3.x). The model's content is replayed unchanged within a
turn, so thought signatures and function-call ids are preserved; function responses echo each call's id and name.

### Optional: Claude

`AGENT_AGENT=anthropic` keeps the earlier Claude Opus 5.5 adapter for backwards compatibility. It is never the
default and never a fallback. It needs `ANTHROPIC_API_KEY` and costs more; `AGENT_LLM_MODEL` / `AGENT_LLM_EFFORT`
apply to it only. Live check: `uv run --env-file .env python -m evals.run --provider anthropic --smoke`.

## Demo: one backend, one session (no API key)

Everything in the demo runs on one scripted agent behind the dev reference host backend that signs each request,
with the contract stub as geo and simulation service. The model is a fixed script (labelled "Відповіді заготовлені
заздалегідь, це не ШІ." in the widget), every value is synthetic demo data, and nothing here is live Claude or live NASA data.

Once: `uv sync`, `npm --prefix frontend install`, and the FAO-56 index (`uv run python -m agent_service.knowledge fetch`,
then `uv run python -m agent_service.knowledge build --no-embed`). Pick two different random strings of at least 32
characters as `K1` (API key) and `K2` (signing key), e.g. `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Then, each in its own terminal:

```bash
uv run python -m integration_stub --port 8100
```

```bash
AGENT_AGENT=scripted-llm AGENT_PORT=8002 AGENT_RAG_MODE=lexical AGENT_API_KEY=$K1 AGENT_CONTEXT_SIGNING_KEY=$K2 AGENT_REQUIRE_CONTEXT_ATTESTATION=true AGENT_GEO_PROVIDER=http AGENT_GEO_URL=http://127.0.0.1:8100/geo AGENT_SIM_PROVIDER=http AGENT_SIM_URL=http://127.0.0.1:8100/sim uv run python -m agent_service
```

```bash
cd frontend && FS_BFF_AGENT_KEY=$K1 FS_BFF_SIGNING_KEY=$K2 npm run dev
```

Open `http://localhost:5173/?host=full&backend=signed` (the demo host page; Poltava is selected) and ask, in order. The default `http://localhost:5173/` is a blank page with only the assistant, and `/phone.html` shows it in a phone frame:

| # | Say / do | What it shows |
|---|---|---|
| 1 | «Що відомо про моє поле?» | field facts with their source (SMAP `SPL4SMGP`, GPM, SoilGrids, NASADEM, HLS) and the ML estimate marked as such |
| 2 | «Покажи вологість ґрунту.» | water facts; the host switches on the SMAP layer (revision 3 → 4) |
| 3 | «Що каже посібник ФАО про водний стрес (нестачу води для рослин)?», then open «Джерело 1» | two verified Chapter 8 quotes; the source detail with the exact sentence |
| 4 | «Поливу немає, хочу зменшити витрати на добрива.» | a proposed condition (nothing saved) and "already confirmed" for irrigation; Confirm → revision 5 |
| 5 | «Порівняй дві сівозміни під час посухи.» | drought is not offered by the service: nothing computed, two reply buttons |
| 6 | click «Порівняй сівозміни для варіанту: типові умови.», then «Відкрити порівняння сівозмін» | a synthetic comparison for `typical`, computed for revision 5 |
| 7 | switch the field to Mykolaiv | a fresh conversation: no Poltava chips, panel or results |

The same journey is the Playwright test "unified demo journey" (`FS_E2E_LIVE=1 npx playwright test e2e/live.spec.ts`
in `frontend/`, with the services above and the other backends of that spec running); its screenshots are
`frontend/docs/screenshots/demo-*.png`.

## Service integration (M5)

The agent talks to member 1's geo service and member 2's simulation service through narrow ports
(`agent_service/integrations/`), chosen by configuration:

| Setting | `off` (default) | `fixture` | `http` |
|---|---|---|---|
| `AGENT_GEO_PROVIDER` | no geo calls; `get_field_facts` reads only the request's `FieldContext` | M0 synthetic profiles in process | `AGENT_GEO_URL` (e.g. the stub `http://127.0.0.1:8100/geo`) |
| `AGENT_SIM_PROVIDER` | `compare_rotations` is **not offered**; the agent says simulation is not connected | M0 synthetic comparison (Poltava, `typical` only) | `AGENT_SIM_URL` (e.g. `http://127.0.0.1:8100/sim`) |

`fixture` is refused in production; `http` needs https in production. A contract stub serves the proposed
v0.1 API from the M0 fixtures, labelled `mode: "stub"`, with fault injection:

```bash
uv run python -m integration_stub --port 8100
```

```bash
AGENT_AGENT=scripted-llm AGENT_SIM_PROVIDER=http AGENT_SIM_URL=http://127.0.0.1:8100/sim AGENT_GEO_PROVIDER=http AGENT_GEO_URL=http://127.0.0.1:8100/geo uv run python -m agent_service
```

```bash
uv run python -m agent_service.integrations.contract_check --geo http://127.0.0.1:8100/geo --sim http://127.0.0.1:8100/sim
```

### Marsden: the first real rotation service (member 2, received 2026-11-12)

The Marsden API (FastAPI, `POST /api/rotations/assessment`, version 2.2.0; run it from its own package on port 8081)
replays recent archive weather (ERA5-Seamless via Open-Meteo, 2017-2026) for three fields in Iowa, USA, and compares
two plans: maize then sunflower, and sunflower then maize. It speaks its own API, so `agent_service/integrations/marsden.py`
puts it behind the team contract, and the stub serves it on /sim (/geo stays the synthetic stub):

```bash
uv run python -m integration_stub --port 8100 --sim-marsden http://127.0.0.1:8081
```

What reaches the agent is only what Marsden computes, labelled as Marsden labels it, as a `live` service:

| Marsden | Contract | Shown to the farmer |
|---|---|---|
| `water_shortfall` (`formula`, FAO-56 water balance) | `water_risk`, `deterministic_calc` | "ризик нестачі води" (calculated); method: share of crop water need not met |
| `modeled_yield_loss_due_to_water` (`model`, FAO-33 Ky) | `yield_loss_water`, `ml_estimate`, not validated | "втрата врожаю через нестачу води" (unchecked estimate) |
| `yield_risk`, `fertilizer` (null today) | absent | nothing: never zero, never estimated |
| scenarios `baseline` / `drought` / `heat` | `typical` / `driest_recent_year` / `hottest_recent_year` | the archive season Marsden picked, named as such (e.g. the driest season 2023) |

Only fields mapped with `--marsden-field HOST_ID=MARSDEN_ID` are computed (default `demo-marsden-01=marsden_demo`,
the demo's "Marsden Farm (Iowa, USA)" field). Any other field gets a failed result, and the agent says the service
does not cover that field. `drought_2024` and `heat_plus_2_5c` are refused, never answered with an archive year.
Farmer conditions are not used by Marsden (it always assumes no irrigation), and every result says so. The other demo
fields (Poltava, Kansas, Mykolaiv) are test fields: Marsden not covering them is expected, not a gap. Still missing
from member 2's side: fertilizer rates, the chance of a harvest loss, and its own `/v1` contract and result storage
(the adapter keeps results for an hour). A new field needs its coordinates and a soil profile to 1 m in Marsden.

How a comparison reaches the farmer: the model calls `compare_rotations(scenario, rotation_ids)` (it has
no field, URL or host parameter) → the server builds an M0 `CompareRotationsRequest` from the request's
field, revision and **confirmed** constraints → the adapter POSTs it with an idempotency key, bounded
timeout and size, no redirects, no automatic retry → `normalize.check_comparison` rejects other fields,
other revisions, other scenarios, expired results, wrong units and demo data labelled live → every score
becomes a cite-only fact (value, unit, uncertainty, provenance, plus scenario / result id / engine in
`method`) → grounding renders "**15% (9–23)** (rotation calculation, case “typical conditions”; computer estimate…;
demo data, not a real measurement)" and rejects "measured/observed" wording for computed values → the model may offer
`panel.open {panel: "comparison", ref_id: <result_id>}`; the service accepts only result ids verified in
that turn, and the host re-checks by fetching the result. Verified tool outputs reach the final message
checks through an internal `TurnEvidence` item that is never streamed.

**Trust boundary (M6A).** Field data in a request is an assertion by whoever sent it. A value claiming
`live`/`cached` data is rendered "(…; from the app, not confirmed by the data service)", adds a
`low_confidence` warning and can never back a "live/real-time" claim, unless the configured geo service
returned the same fact in that turn or the host backend signed the request (M6B, below). Where the geo
service disagrees with the request, the service's value is used. Schema validity is never treated as proof
of origin.

**Signed context (M6B).** The host backend (BFF) signs each request with `AGENT_CONTEXT_SIGNING_KEY`:
`X-FieldShift-Context-Attestation` binds the authenticated farmer (opaque principal), the field, its
revision, the SHA-256 of the exact body and a short expiry (≤ 300 s). Only then are the body's facts
host-attested, and simulation results are isolated per principal. An API key alone, a client boolean, a
`field_id` or any other header is never identity or permission. `AGENT_REQUIRE_CONTEXT_ATTESTATION=true`
(mandatory in production) refuses unsigned requests with 403. Details and a Node signer:
[docs/M6B_TEAM_ONBOARDING.md](docs/M6B_TEAM_ONBOARDING.md); acceptance evidence:
[docs/M6B_INTEGRATION_ACCEPTANCE.md](docs/M6B_INTEGRATION_ACCEPTANCE.md). Each signed request is accepted once (M6C).

**Scenario integrity (M6C).** A simulation result for one scenario is never the answer for another: the agent runs
only the scenarios the farmer named, says when one is not offered and asks before comparing another; `panel.open`
names the result's scenario so the host can check what it fetches. Full browser flow through a dev reference host
backend that signs requests (`?backend=signed`): [docs/M6C_TEAM_HANDOFF.md](docs/M6C_TEAM_HANDOFF.md) §4; evidence:
[docs/M6C_ACCEPTANCE.md](docs/M6C_ACCEPTANCE.md).

`/health/ready` reports `integrations.geo` / `integrations.simulation` (`configured`, `connected`,
`service_mode`, `implementation`, `contract_version`), `demo_mode`, `degraded` and `live_services`. A
ready agent with `degraded: true` still answers; a stub or fixture is never reported as live.
Each external call logs one line: service, operation, request id, status, duration, contract version,
mode, result id, result status, data status, failure category (no payloads, no farmer text).

## Configuration

All settings are `AGENT_*` environment variables, validated at startup. An invalid or unsafe configuration stops the process with a message that never includes secret values. See `.env.example` for every variable with its default.

| Variable | Default | Notes |
|---|---|---|
| `AGENT_ENVIRONMENT` | `development` | `development`, `test` or `production` |
| `AGENT_HOST` / `AGENT_PORT` | `127.0.0.1` / `8000` | |
| `AGENT_CORS_ORIGINS` | localhost:5173, 127.0.0.1:5173, localhost:3000 | Explicit origins only; `*` is rejected; credentials are never allowed |
| `AGENT_AGENT` | `deterministic` | See the table above |
| `AGENT_API_KEY` | unset | Bearer token for `/v1/*`. **Required (≥ 32 chars) in production** |
| `AGENT_TURN_TIMEOUT_S` | `120` | Budget for the whole turn (all model calls and tools) → `upstream_timeout` |
| `AGENT_MAX_BODY_BYTES` | `262144` | Larger bodies → 413 |
| `AGENT_LOG_MESSAGE_TEXT` | `false` | Logs record message length only, unless you turn this on |
| `AGENT_GEMINI_MODEL` / `AGENT_GEMINI_THINKING_LEVEL` | `gemini-3.8-flash` / `low` | `gemini` mode. Thinking `low`, `medium` or `high`; `minimal` is refused |
| `AGENT_LLM_PRICE_INPUT_USD_PER_MTOK` / `_OUTPUT_USD_PER_MTOK` / `_CACHED_INPUT_USD_PER_MTOK` | unset | Optional published prices for the logged `est_usd` estimate (thinking billed as output; cached input at its own price) |
| `AGENT_LLM_MODEL` / `AGENT_LLM_EFFORT` | `claude-opus-5-5` / `medium` | `anthropic` mode only |
| `AGENT_LLM_MAX_ROUNDS` / `_MAX_TOOL_CALLS` / `_MAX_TOOL_CALLS_PER_ROUND` | `6` / `12` / `6` | Loop bounds per turn |
| `AGENT_LLM_MAX_OUTPUT_TOKENS` | `4096` | Per model call |
| `AGENT_LLM_MAX_CONTEXT_CHARS` | `60000` | System prompt + context budget (characters, not tokens); oldest history dropped first |
| `AGENT_LLM_MAX_RETRIES` / `_REQUEST_TIMEOUT_S` / `_TOOL_TIMEOUT_S` | `2` / `60` / `5` | SDK retries (408/429/5xx/connection), per-call and per-tool timeouts. Apply to every real provider |
| `AGENT_LLM_MAX_REPAIRS` | `1` | Grounding rewrites before unsupported sentences are removed |
| `AGENT_LLM_REFUSAL_FALLBACK` | `true` | `anthropic` only: server-side `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) |
| `AGENT_LLM_STRICT_TOOLS` | `true` | `anthropic` only: `strict: true` tool schemas; arguments are re-validated on the server either way |
| `AGENT_GEO_PROVIDER` / `AGENT_GEO_URL` | `off` / unset | `off`, `fixture` (not in production), `http` (needs the URL; https in production) |
| `AGENT_SIM_PROVIDER` / `AGENT_SIM_URL` | `off` / unset | same for the simulation service |
| `AGENT_INTEGRATION_TIMEOUT_S` / `AGENT_SIM_TIMEOUT_S` | `8` / `30` | geo and capability calls / one comparison |
| `AGENT_INTEGRATION_MAX_BYTES` | `2000000` | larger responses are rejected |
| `AGENT_INTEGRATION_TOKEN` | unset | server-to-server bearer for geo / simulation; never sent to a browser |
| `AGENT_CONTEXT_SIGNING_KEY` | unset | HMAC key shared only with the host backend (≥ 32 chars, ≠ `AGENT_API_KEY`); verifies per-request context attestations |
| `AGENT_REQUIRE_CONTEXT_ATTESTATION` | `false` | `true` refuses unsigned requests (403). **Required in production.** The removed `AGENT_CONTEXT_ATTESTED` stops startup |

**Before any public deployment:** `AGENT_API_KEY` is a shared secret for server-to-server or demo gating, not user authentication. A public app needs a backend-for-frontend or real user auth in front of this service, plus rate limiting (not implemented).

## API

| Method and path | Purpose |
|---|---|
| `GET /health/live` | Process is up. Touches nothing else. |
| `GET /health/ready` | 200 `{"status":"ready","agent":…}` once the agent initialised; 503 otherwise. Does not call the LLM. |
| `POST /v1/agent/message` | `AgentRequest` → one `AgentMessage` (JSON) |
| `POST /v1/agent/stream` | `AgentRequest` → `AgentEvent`s as Server-Sent Events |

Both endpoints take the M0 `AgentRequest` with `Content-Type: application/json`. Errors use the M0 `ErrorEnvelope`:

| Status | `error.code` | When |
|---|---|---|
| 401 | `invalid_request` (`error.unauthorized`) | Bearer token missing or wrong (only when `AGENT_API_KEY` is set) |
| 400 | `invalid_request` (`error.context_too_large`) | The field context does not fit `AGENT_LLM_MAX_CONTEXT_CHARS` |
| 413 / 415 / 422 | `payload_too_large` / `invalid_request` | Body too big, not JSON, or not matching the contract |
| 429 | `rate_limited` | Provider rate limit after SDK retries; `retryable: true` |
| 502 | `llm_error` | Model output incomplete (`error.llm_incomplete`, retryable), loop stopped (`error.agent_no_progress`, `error.agent_round_limit`), bad credentials (`error.llm_auth`) or request (`error.llm_bad_request`) |
| 503 | `upstream_unavailable` | Agent not ready, or provider overloaded / 5xx (`retryable: true`) |
| 504 | `upstream_timeout` | Turn or provider timeout (`retryable: true`) |
| 500 | `internal` | Agent bug or contract violation; details only in the server log |

On `/stream`, errors after the stream starts arrive as one `error` event with the same `code` and `message_key`.

```bash
curl -s http://127.0.0.1:8000/v1/agent/message -H "Content-Type: application/json" --data @examples/agent_request.scenario_b.json
```

```bash
curl -N http://127.0.0.1:8000/v1/agent/stream -H "Content-Type: application/json" --data @examples/agent_request.scenario_b.json
```

## Streaming protocol

Unchanged from M1. Each frame is `event: <AgentEvent.event>`, `data: <AgentEvent JSON>`, `id: <n>`. The order is `turn.started`, then `tool.started`/`tool.finished` pairs and `text.delta`s, then **exactly one** `turn.completed` or `error`. Lines starting with `:` are keep-alive comments. Render the final state from `turn.completed`. The tool events are the farmer's progress list, so the model's own slips stay out of it: a call refused before it runs (unknown tool, malformed arguments, an identical repeat) gets no events, and a refusal the answer explains (a wrong date, a scenario the service does not offer) finishes as `ok`; only real failures finish as `error` or `timeout`. The tool traces in the logs keep the real outcome.

**LLM mode is validated incremental delivery, not raw token streaming.** Tool events stream as tools run. The answer text is held back until it has passed the grounding checks, then sent as paragraph deltas just before `turn.completed`. A turn that fails never streams any answer text. During a model call the stream is quiet apart from keep-alive pings (every 15 s).

Frontend integration (typed `fetch()` client, Ajv validation, handling UI actions as proposals): see `examples/client.ts` and the M1 notes in the **Frontend integration** section below.

## How a turn works (LLM modes)

```
AgentRequest ─▶ AgentService (M1: deadline, cancellation, final output checks)
                  └▶ LLMAgent.respond()                                   agent_service/llm_agent.py
                       1. context envelope: trusted system rules + JSON data blocks      prompts.py
                       2. provider.complete(system, transcript, tools)   llm/provider.py, llm/gemini_provider.py
                       3. stop reason:
                            tool_use    → validate + run each call (registry) → results back → step 2
                            end_turn    → grounding check → (one repair round) → AgentMessage
                            max_tokens  → error event, retryable (incomplete output is never shown or run)
                            refusal     → polite canned reply, nothing proposed
                            other       → error event
                       4. text deltas (only after grounding), then the message
```

**Provider layer** (`agent_service/llm/`). The loop only sees neutral types (`ModelResponse`, `ToolCall`, `ToolResult`, `Usage`, normalised stop reasons, `ProviderError` kinds). Gemini structures stay in `gemini_provider.py` (see **Gemini 3.8 Flash** above), which maps finish reasons (`STOP` → end turn or tool use, `MAX_TOKENS` → incomplete, `SAFETY` / `PROHIBITED_CONTENT` / `BLOCKLIST` / `SPII` / `RECITATION` and blocked prompts → refusal, `MALFORMED_FUNCTION_CALL` and anything else → unexpected stop) and SDK errors (429 → rate limited, 401/403 → auth, 408/504 and transport timeouts → timeout, 5xx and connection errors → unavailable, 400 token-limit → context too large), logging only the status code and status name. Anthropic message structures stay in `anthropic_provider.py`, which:
- uses `client.beta.messages.create` (non-streaming, `max_tokens` 4096) with `output_config.effort` and automatic tool choice (Opus 5.5 rejects forced tool choice) and `strict: true` tools;
- replays assistant content unchanged (thinking blocks included), and drops pre-fallback model-internal blocks after a refusal fallback;
- maps SDK errors (`RateLimitError`, `APITimeoutError`, `APIConnectionError`, `AuthenticationError`, `InternalServerError`, `OverloadedError`, `APIStatusError`) to provider error kinds, logging only status and error type.

The **scripted provider** plays the model from a list of responses or a function, so the same loop, tools and grounding run offline.

**Tools** (`agent_service/tools.py`). The model chooses when to call them; the server decides what they can do:

| Tool | What the server does |
|---|---|
| `get_field_facts(domains, fact_ids)` | Returns facts from **this request's** field context only, with unit, uncertainty, availability, product, version, period, model-validation status and a `cite_as` token. There is no field-id parameter; unknown ids come back as `not_found`. Retrieved facts become citable for this turn. |
| `propose_constraints(constraints[])` | Validates each item against the M0 `Constraint` union. `explicit` needs evidence copied exactly from the farmer's text. `inferred` needs a confidence. Contradictory values in one turn are rejected for that type. A change to a confirmed constraint is accepted and reported as `supersedes`. Ids, `status: "proposed"` and source are set by the server. Nothing is saved. |
| `propose_ui_actions(actions[])` | Accepts only the M0 action types (minus `select_rotations`, which needs simulation results). It checks ids against the context and sets the revision itself. It forces `chip` for constraint and scenario actions and for ML-estimate layers, rejects markup in labels, and allows at most 4 actions per turn. |

Every call is re-validated with Pydantic in JSON mode. Unknown tools, malformed or extra arguments, and identical repeated calls return structured `is_error` results the model can recover from. Two rounds without progress end the turn. Each call is logged as one trace line (turn id, call id, tool, status, duration, error category, argument summary, returned ids). The trace never contains farmer wording or prompts.

## Grounding

The model never writes a field value itself. It writes `{{fact:<id>}}`, and the server renders the value with unit and uncertainty, plus a short note in plain words only where the reader could take the number for something it is not: "(неперевірена оцінка)", "(розрахунок)", the rotation case, "(з ваших слів)", a value the data service did not confirm. Plain satellite and weather values get no note: their source, exact product, version and period are in each fact's provenance and in the widget's "data used" panel, and the prompt asks the model to name the period in words. Demo data is said once, in a notice under the answer; a demo value is marked "(демо)" in the text only when the same text also gives real values (`grounding.origin_note`). After the final answer:

1. Every fact token must name a fact this turn actually retrieved.
2. Numbers the model wrote are classified. Measurements (a unit or %), decimals, integers above 10, and any number after "pH" are unsupported claims. Years, dates, small counts, list markers, product names (`SMAP L4`, `FAO-56`, `v008`), inline code, and integers the farmer wrote are allowed.
3. Prose patterns catch claims the system can't back: a simulation result, "live" or "real-time" readings, lab values from a photo.
4. On a violation the model gets one rewrite. If it fails again, the offending sentences are removed and the message carries an `ungrounded_number` warning. If nothing is left, a safe canned reply is sent.
5. The M1 service checks still run on the final message: facts must match the context verbatim, constraint evidence must be in the farmer's text, and actions are filtered.

**What this does not guarantee.** Hallucinations are reduced, not eliminated. Not caught:
- numbers written as words ("twenty percent");
- qualitative claims ("the soil is too dry for maize");
- wrong agronomic advice that contains no number;
- claims paraphrased outside the patterns.

The number classifier is conservative: it can flag harmless numbers (for example "30 m resolution"), which costs a repair round. Sentence removal is heuristic. These limits are covered by tests in `tests/test_grounding.py` and the `dr_*` eval cases.

## Knowledge base (M3)

### Sources and licence

Only allowlisted pages are ingested (`agent_service/knowledge/sources.py`): FAO Irrigation and Drainage Paper 56, *Crop evapotranspiration* (Allen, Pereira, Raes, Smith; FAO 1998), Chapters 1, 5, 6, 7 and 8 from `https://www.fao.org/4/x0490e/`.

FAO's permissions page allows use for study, research, teaching and **non-commercial** products, with acknowledgement of FAO as source and copyright holder and **without implying FAO endorsement**. Other reuse, including commercial deployment, needs an FAO licence request. Therefore:
- the fetched HTML and the built index (both containing FAO text) live under `data/`, which is git-ignored;
- every citation carries the publication, URL and a short attribution;
- quotes are limited to 300 characters.

**Ukrainian regional recommendations (added 2026-11-10).** Two text PDFs of NAAS institutes are also allowlisted, for
practice questions (topic `crop_practice`): *Проведення весняно-польових робіт в АПК Черкаської області в умовах 2024
року* (Черкаська ДСГДС ННЦ «Інститут землеробства НААН»; spring work, Forest-Steppe) and *Підготовка та проведення
посіву озимих сільськогосподарських культур в агроформуваннях Житомирської області в умовах 2026 року* (Інститут
сільського господарства Полісся НААН; winter crops, Polissia). They are read with `pypdf`, one chunk per page part, each
citation naming the chapter and crop path and the page (`#page=N` link). Their reuse terms are not stated in the
documents: they are quoted briefly with the institution and page, in this non-commercial prototype, and stay under the
git-ignored `data/` like FAO text. Not added: the 2019 Southern Steppe booklet, whose PDF text falls apart into single
characters ("14,0" read as "1", "4,0"). Known limit: pypdf drops superscripts ("2–3°" may read "2-30"), which is why
every citation links to its page. Ukrainian words are searched by stem (`соняшни*`), crops by their glossary stem
(`пшениц*`), FAO science terms exactly; the answer must name the region and use a passage only for its own crop.

### Build it

```bash
uv sync --group rag
```

```bash
uv run python -m agent_service.knowledge fetch
```

```bash
uv run python -m agent_service.knowledge build --no-embed
```

`--no-embed` builds a lexical-only index (no model, seconds). For hybrid retrieval, download the pinned models once (about 2.3 GB each) and build with embeddings, about 12 minutes on a 6-core laptop CPU:

```bash
uv run python -c "from huggingface_hub import snapshot_download as s; s('BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181', allow_patterns=['*.json','pytorch_model.bin','sentencepiece.bpe.model','1_Pooling/*']); s('BAAI/bge-reranker-v2-m3', revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e')"
```

```bash
uv run python -m agent_service.knowledge build
```

Add `--model-cache <folder>` (or set `AGENT_RAG_MODEL_CACHE`) when the models live outside the default Hugging Face cache. To search from the command line:

```bash
uv run python -m agent_service.knowledge search "Kc mid maize" --mode hybrid
```

### How it works

- **Fetch**: exact-URL allowlist, `https` only, same-host redirects, 30 s timeout, 3 retries with backoff, 2 MB cap, 1 s pause between pages, sha256 per page in `fetch_manifest.json`. A cached page that no longer matches its checksum fails the build.
- **Parse** (lxml): keeps the heading hierarchy and anchors, paragraphs, numbered equations and footnotes. Tables become grids, with row and column spans resolved, group rows ("i. Cereals") carried to every crop, and units rows kept in the header. Navigation and link lists are dropped. Images that carry text (4 table column headers such as Table 12's "Kc ini", and 27 equations) are replaced only by transcriptions a person verified (`VERIFIED_IMAGE_TEXT`). Any other image becomes `[image not transcribed]`, and a table whose header still contains one is flagged as incomplete, never trusted silently. About 98–99.5 % of each page's non-navigation words reach the index; a test checks this.
- **Chunk**: chunks never cross a heading. An equation stays with the paragraph before it and its "where …" explanation. Tables are chunked by crop group, and every row repeats its column names ("Crop: Maize, Field (grain) (field corn) | Kc ini^1: — | Kc mid: 1.20 | …"), so a number never loses its crop, stage or unit. Footnotes become `table_notes` chunks. Chunk ids are derived from content: re-ingesting unchanged text keeps the ids, changed text gets new ids, so an old citation can never point at new text.
- **Index**: one SQLite file with FTS5 (BM25, Porter stemming) and float32 embeddings. It is built into a temporary file and swapped in atomically, and unchanged chunks reuse their embeddings. At runtime the index is copied into memory, so it can be rebuilt while the service runs. The schema version and source list are checked at load: a stale index is refused.
- **Retrieve**:
  1. BM25. User text only ever becomes quoted FTS5 terms, never query syntax. Ukrainian queries are rewritten for this leg with a fixed stem glossary (written before the benchmark); the original query goes to the dense model.
  2. bge-m3 dense, brute-force cosine (349 × 1024: milliseconds).
  3. Reciprocal Rank Fusion, k = 60 (ranks, not raw scores).
  4. Optionally, bge-reranker-v2-m3 on the fused top 20.

  If a model fails to load or crashes at query time, retrieval falls back a level and says so in the result and in `/health/ready`. Relevance ranks order passages; they are **not** the probability that a statement is true. Reranker scores are the model's sigmoid outputs in [0, 1]: they order passages and mean nothing about whether an agricultural claim is correct.

### Retrieval quality and cost (measured)

```bash
uv run python -m evals.retrieval.run --model-cache D:/field-shift-models/hf
```

`evals/retrieval/benchmark.json` holds 57 hand-written questions: 27 English, 24 Ukrainian, and 6 the corpus cannot answer. Each answerable question names its evidence as a page plus phrases that must occur in the chunk. These anchors are resolved against the index when the benchmark runs; ranking code never sees them, and no benchmark text is in the indexed documents. Results from 2026-11-06 (349 chunks, bge-m3 float32), on the 51 answerable questions:

| Variant | Hit@1 | Hit@3 | Hit@5 | Recall@10 | MRR@10 | en Hit@5 | uk Hit@5 | uk MRR@10 |
|---|---|---|---|---|---|---|---|---|
| BM25, no glossary | 0.294 | 0.451 | 0.510 | 0.569 | 0.387 | 0.741 | 0.250 | 0.177 |
| BM25 + uk glossary (`lexical`) | 0.431 | 0.627 | 0.686 | 0.833 | 0.551 | 0.741 | 0.625 | 0.519 |
| bge-m3 dense (`dense`) | 0.549 | 0.725 | 0.765 | 0.843 | 0.653 | 0.815 | 0.708 | 0.594 |
| RRF fusion (`hybrid`) | 0.549 | 0.784 | 0.804 | 0.873 | 0.667 | 0.778 | 0.833 | 0.659 |
| RRF + reranker top 20 (`hybrid_rerank`) | **0.686** | **0.843** | **0.863** | **0.882** | **0.776** | **0.852** | **0.875** | **0.719** |

Recall@5 and the per-language Hit@1/Recall@10, every ranking, latency and memory are in `evals/reports/retrieval-latest.md` and `.json` (chunk ids and scores only, no FAO text). Reranking does not win everywhere: Ukrainian Recall@10 drops from 0.875 (hybrid) to 0.833. 51 questions written by one person are a small benchmark: treat differences of a few points as noise.

Cost on this machine (Ryzen 5 6600H, 6 cores, CPU-only torch, 15 GB RAM):

| | Measured |
|---|---|
| BM25 per query | median 0.6 ms, p95 1.1 ms |
| bge-m3 query embedding | median 0.5 s, p95 4.7 s (first query after load about 4–5 s) |
| Reranking 20 passages (batch 4) | median **33.5 s**, p95 45.1 s |
| Model load | bge-m3 57 s, reranker 11 s; a `hybrid` service is ready about 1 minute after start |
| Process memory (RSS) | 52 MB without models; 0.8 GB after loading bge-m3, 2.0 GB peak while embedding queries; 2.9 GB after the reranker loaded |
| Index | 3.5 MB SQLite; build with embeddings 11.5 minutes |

**Recommendation:** reranking gives the best quality (Hit@1 +0.14 over hybrid) but on CPU it takes longer than the 20 s `search_knowledge` timeout, so a turn would fall back to "unavailable". Run **`hybrid`** on CPU machines, and `hybrid_rerank` only on a GPU or with a smaller `AGENT_RAG_RERANK_TOP` after measuring it there. `lexical` needs no model at all and remains the default so the service starts anywhere.

**No relevance threshold:** reranker top-1 scores of some answerable questions (0.00–0.02) overlap with those of the unanswerable ones (0.00–0.01), so a cut-off would discard good evidence, and there is no held-out set to tune one honestly. Abstention is left to the model plus the citation checks; the six unanswerable questions are reported, not scored.

### Citations

The model writes `{{cite:<chunk_id>}}`, optionally with an exact quote: `{{cite:<id>|"words from the passage"}}`. The server:
1. **Checks the id:** it must exist in the index and have been returned by `search_knowledge` in this turn. Otherwise it is `fabricated_citation` or `citation_not_retrieved`.
2. **Checks any quote:** it must be an exact span of the passage, whitespace-insensitive (`fabricated_quote`).
3. **Rejects URLs** the model writes itself (`unverified_url`). Titles, links and locators come only from the index.
4. **Checks numbers** in a citing sentence that the grounding policy treats as claims. Each must occur in the cited passage. For table passages it must be in the row of a crop the sentence names (field vs sweet maize included) and in the column of a stage the sentence names (ini, mid, end, rooting depth, depletion, height; English and Ukrainian). Otherwise: `number_not_in_source`, `wrong_table_row` or `wrong_table_column`.
5. **Rejects** a cited sentence that also talks about "your field" and carries source numbers (`general_source_as_field_data`), and a sentence sharing no vocabulary with its passage (`citation_unrelated`).
6. **Renders** valid tokens as `[n]` and returns M0 `Citation` objects: publication, chapter, section, table, URL with anchor, an exact quote of at most 300 characters (the model's verified quote, or the passage sentence or row closest to the claim), and the licence note.

Violations go through the M2 repair round and then sentence removal, as for numbers.

**What citations do not guarantee:** a valid citation does not prove the sentence's reasoning follows from the passage. A model can still misread a correctly cited passage, generalise beyond it, or leave out a caveat; none of that is checked. An unqualified crop name ("maize") accepts either maize row. The checks above catch the structured mistakes (invented sources, wrong numbers, wrong rows and columns, field misuse), not every misinterpretation.

## Evaluation suite

```bash
uv run python -m evals.run
```

The suite runs 109 cases (`evals/cases.py`, `evals/knowledge_cases.py`, `evals/integration_cases.py`) through the real service, agent loop, tools and grounding, with the scripted provider playing well-behaved and adversarial models. It checks outcomes (events, final message, what the model was sent), plus contract invariants on every event against the **exported JSON Schema**. It writes `AGENT_DATA_DIR/evals/reports/scripted-latest.md` and `.json` with pass/fail by category and failure categories. Categories: tool selection, constraint extraction, data reliability, security, provider behaviour, knowledge, integration. Integration cases use the real adapters (fixture, and HTTP against the contract stub with injected faults) and add invariants: message facts must come from the context or the turn's verified evidence, result references must point at a verified result, and simulation facts from a test service must be labelled fixture.

Scripted results validate orchestration and safeguards, **not** a real model's judgement. Live model quality needs the live run:

```bash
uv run --env-file .env python -m evals.run --provider gemini --smoke
```

`--smoke` runs the 6 smoke cases with tight limits; without it the run covers every `live` case (more calls, more cost: ask before running it). Live runs apply outcome checks only. Without credentials they exit with code 3 and run nothing. `--provider anthropic` does the same on Claude.

## Development

One command before handing over (no paid API calls; `--quick` skips the frontend build and browser tests):

```bash
uv run python scripts/verify.py
```

Live Gemini smoke suite (6 cases, at most 4 model calls each, 2048 output tokens per call; records the model id,
latency, input/output/thinking tokens and an estimated cost; exits 3 and runs nothing without `GEMINI_API_KEY`):

```bash
uv run --env-file .env python -m evals.run --provider gemini --smoke
```

Offline backup of the git history: `uv run python scripts/backup.py --dest <another drive>` (see
`docs/SOURCE_CONTROL_CHECKPOINT.md`). Team handoff: `docs/M6_TEAM_HANDOFF.md`.

```bash
uv run pytest
```

```bash
uv run python -m contracts.export --check
```

```bash
npm run gen:ts
```

```bash
npm run check:ts
```

| Test file | Covers |
|---|---|
| `tests/test_contracts.py` | M0 fixtures, invalid samples, schema freshness |
| `tests/test_api.py`, `test_service.py`, `test_dev_agent.py`, `test_config.py` | M1: HTTP, SSE, errors, auth, CORS, limits, service checks, dev agent |
| `tests/test_llm_agent.py` | LLM mode through HTTP; disconnect during a model call (raw ASGI, as uvicorn); cancellation during a tool; tool timeout; isolation of concurrent turns; prompt trust layout |
| `tests/test_anthropic_provider.py` | Real SDK with a mocked HTTP transport: request shape, thinking replay, fallback boundary, stop reasons, error mapping without key leakage, strict-schema compatibility |
| `tests/test_grounding.py` | Number classification, claim patterns, token rendering, redaction |
| `tests/test_evals.py` | The 108 agent eval cases (61 M2 + 22 knowledge/citation + 22 M5 integration/relevance + 3 M6A) |
| `tests/test_trust_boundary.py` | M6A: forged live values (rules and LLM agents), live-claim blocking, verification by a live service, service-wins conflicts, attestation, source categories, simulation identity/scenario, idempotency scope, expired replay, production refusing stubs, cache invalidation, result replay across fields/revisions |
| `tests/test_contract_check.py` | M6A: checker targets, not-performed services, field paths, remediation per category, no token in reports, generated examples |
| `tests/test_live_readiness.py` | M6A: the live smoke suite's case selection, limits, usage capture and credential gate (offline; no live call) |
| `tests/test_integrations.py` | M5: envelopes, fixture/unavailable/HTTP adapters (in process and over a real socket), validation boundary (SMAP/POWER/SoilGrids rules, units, zero vs missing, coordinates, demo/live labels), registry and readiness, unsafe settings, result references, forged facts, scenarios A, D, E, H, I, concurrent fields, confirmed-only constraints, tool steering |
| `tests/test_knowledge.py` | Real FAO pages: table structure, footnotes, equations, text coverage, stable ids, checksums, stale/missing index, hostile FTS input, filters, benchmark anchors |
| `tests/test_rag.py` | Citation rules on the real Table 12 chunk, quotes, URLs, `Citation` contract, retrieval fallbacks, SSE with `search_knowledge`, per-turn evidence isolation, cancellation during search |

## Frontend integration

The assistant widget, its integration contract (`<FieldShiftAssistant>` props, `onAction`, `actionProblem`), the demo host and how to run it next to this service are in [frontend/README.md](frontend/README.md).

`examples/client.ts` is a typed client (`sendMessage`, `streamTurn`, `runTurn`) checked by `npm run check:ts` against `contracts/ts/index.d.ts`. UI actions are proposals: the host drops actions with a stale `field_context_revision` and re-checks ids against its live store. `trigger: "auto"` is only used for display changes the farmer asked for; anything changing the comparison arrives as a `chip`. To validate events at runtime, use Ajv 2020 with `ajv-formats` and `ajv.addKeyword("tsType")` on `contracts/schema/fieldshift-contracts.schema.json`.

## Known limitations (M5)

- **No live Gemini call yet:** the Gemini path is verified through the real SDK with mocked HTTP only (request
  shape, thought-signature replay, function-call ids, errors). Real tool-choice quality, Ukrainian answers, latency
  and cost per turn are unmeasured until the smoke run has a key. The same holds for the optional Claude path.
- **Grounding:** see the section above. Prose is only pattern-checked.
- **Provider portability:** two real adapters (Gemini, Claude) share the interface, both verified offline only.
- **Context budget** is measured in characters, not tokens. History comes from the client and is passed as quoted data, so a forged history can mislead but cannot override rules.
- **Model calls are non-streaming.** A long call shows only keep-alive pings until it finishes.
- **Tools run one at a time** in the order the model gave. They are in-process and fast; parallel execution would complicate the per-turn state that later calls depend on.
- **Not implemented:** simulation, image and voice, live NASA data, rate limiting, user-level auth. `select_rotations` actions are not offered.
- **Demo agents:** the rules agent (`deterministic`) now also treats "show <topic>" as a map request when the field has a matching layer, and recognises yield/simulation requests to say the simulation service is not connected. The scripted demo model (`scripted-llm`) sends knowledge questions (FAO, Kc, water stress…) to `search_knowledge` and quotes whole passage sentences with cite tokens. Both remain demos, not models.
- **No teammate service is connected.** The geo and simulation contracts are proposals; the HTTP path is verified only against the contract stub (in process and over TCP). `get_timeseries` is not implemented.
- **Citation relevance** is a lexical signal: a `low_confidence` warning when a cited passage shares no key term with the question. It cannot judge scientific sufficiency.
- **Knowledge base:** one publication (FAO-56, five chapters); crops and regions outside it are not covered. Lexical retrieval of Ukrainian questions depends on a hand-written glossary. Citation checks are structural (see **Citations**). FAO terms restrict commercial use. The FAO-56 tables are general guidance, not this field's values; numeric Kc and rooting-depth data for simulation belong in the simulation team's validated dataset, not in RAG.
- **Resources:** bge-m3 and the reranker are about 2.3 GB each on disk (here stored on `D:` via `AGENT_RAG_MODEL_CACHE`, because `C:` had under 1 GB free). CPU reranking is too slow for interactive use (see **Retrieval quality and cost**). Dense first-query latency after load is about 4–5 s.
- **No live hybrid run with Claude:** hybrid retrieval was verified end to end with the scripted provider only.
- **`/message` is not cancelled on client disconnect** (bounded by the turn timeout); `/stream` is.
- **FastAPI 0.162 race:** its SSE keep-alive task can raise `BrokenResourceError` when a client disconnects while a frame is queued. The request middleware treats exactly that case as a normal disconnect (tested); agent work is cancelled either way.
