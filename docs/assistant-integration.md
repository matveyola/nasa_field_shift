# Field Shift assistant integration

The Assistant page and field signals use the Field Shift API. The API
loads the selected field and saved evidence from its own database, signs the
exact request body, and sends it to the private Python agent runtime. The runtime checks the bearer key, field,
revision, expiry, body hash and one-time nonce. The browser receives the reply
and supported actions through the `/api/agent` routes.

## Run locally

```powershell
python infra/configure-agent.py
docker compose --env-file .env --env-file .env.agent --profile agent up --build -d
```

If `.env` is absent, use `.env.example` as the first environment file. The
helper prints the applicable command and preserves existing keys. Compose
keeps the agent on its private network and publishes the web app on loopback.

The profile starts in `scripted-llm` mode. The agent runs its tool
loop and reads Field Shift evidence using a local scripted model. The UI labels
this as a demo mode. A plain Compose run without the profile and agent environment
file uses the template provider.

For a paid Gemini model, set `FIELD_SHIFT_AGENT_MODE=gemini` and `GEMINI_API_KEY`
in your local `.env` and recreate the agent service. Set
`FIELD_SHIFT_AGENT_GEMINI_MODEL` to a model available to your project if needed.
The key is passed to the agent container only. This mode sends the selected field
context and recent chat history to the model provider. The status endpoint reports
an unavailable assistant if initialization fails. The API keeps that error visible.

## Use an OpenAI-compatible endpoint

Set the provider's API base URL, exact model ID, and key in the local `.env`.
For a gateway that exposes `gpt-luna`, use these settings with your own values:

```dotenv
FIELD_SHIFT_AGENT_MODE=openai
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_MODEL=gpt-luna
OPENAI_API_KEY=your-provider-key
```

On the first upgrade, rebuild the agent, API and web services. After that, a
provider configuration change only needs the `agent` service recreated.

```powershell
docker compose --env-file .env --env-file .env.agent --profile agent up --build -d agent api web
```

The official Python SDK appends `/chat/completions` to the base URL. Use the
API root supplied by the provider, including its version prefix if it has one.
The model needs to support Chat Completions function tools and tool result
messages for access to Field Shift data. Set `OPENAI_TOKEN_LIMIT_FIELD=max_tokens`
if your gateway uses that field for output limits. The default is
`max_completion_tokens`. For an HTTP service running on your computer, use
`http://host.docker.internal:<port>/v1` from Docker.

To use GPT-6 Luna directly through OpenAI, set `OPENAI_BASE_URL=https://api.openai.com/v1`,
`OPENAI_MODEL=gpt-6-luna`, and `OPENAI_REASONING_EFFORT=none`. GPT-6 Luna
requires this reasoning setting for function tools through Chat Completions.
Leave `OPENAI_REASONING_EFFORT` unset to use the gateway's default.
[OpenAI GPT-6 API guide](https://developers.openai.com/api/docs/guides/latest-model)

The endpoint and model must both be set explicitly. The key is only passed to
the agent container. In this mode the configured endpoint receives the selected
field context and recent chat history.

## Evidence and actions

- The agent can explain saved FAO-56 root-zone forecasts, historical water runs
  explicitly linked to the conversation, and sampled Sentinel-2 NDVI. Units, dates,
  source and calculated versus observed provenance accompany the supplied facts.
- A saved historical run reaches the assistant only when the farmer selects it in Analytics → Water and
  clicks «Ask agent about this run». The widget's base URL then ends in
  `/assistant/{field}/{season}/{scenario}/runs/{run_id}` (a path segment, because the widget
  appends `/health/ready` and `/v1/agent/stream`). The API loads the run, checks the field is
  the caller's, and that the run is a completed historical (not demo) water balance for the
  current boundary, season and scenario with valid provenance. It then builds and signs the
  context from that run, without the current forecast or NDVI. A missing, unfinished or stale
  run is a 404/409 shown to the farmer, never replaced by another run. Without a selection,
  the latest run is never attached. A new field, season, scenario or run starts a new conversation.
- Map and chart chips point to the same selected field. The API allows only supported
  navigation actions from the signed context. The UI checks the boundary revision
  before opening a chip. Existing conversations are preserved by migration `0005`.
- Rotation diagnostics remain available in Analytics; Strategies owns allocation and
  the separately gated native and handoff model flows. The agent's
  proposed simulation contract and external geo connector are disabled in this
  profile. The agent cannot start a comparison, and it cannot save a constraint.
- Planning constraints the agent proposes (for example "no irrigation", "exclude
  sunflower") are recorded only when the farmer confirms or rejects one in the assistant.
  Each proposal offers three choices. «Confirm» saves it as a confirmed condition.
  «Reject» saves the farmer's rejection (`status: "rejected"`), so it is not taken as
  agreed. «Hide» only hides it in that conversation: nothing is sent or saved. A decision
  shows as confirmed or rejected only once the field context the API rebuilt holds it.
  The agent's own actions never confirm or reject for the farmer. «Edit» lets the farmer
  change a proposal before deciding; it saves nothing, and an edited value is sent as the
  farmer's own choice (`farmer_ui`, no quoted words). Crops can be edited only to
  `maize`, `soybean` or `sunflower`, the planning crops whose identity the agent shares
  (`apps/web/.../constraintCrops.ts`); a proposal naming other crops (e.g. `winter_wheat`)
  can be decided as proposed but not edited. A canonical crop vocabulary across planning
  and the agent is still to be agreed.
  The widget sends that `compare.set_constraints` decision to
  `POST /api/agent/constraints/{field}/{season}`. The API checks the field is the caller's,
  that the action was made for this field and its current boundary revision, and every
  constraint against the agent contract. A `proposed` constraint, including an agent
  inference, and an `auto` envelope are refused (defence in depth: the signed-in browser
  sends the request, so this does not prove a physical click). Decisions are stored per
  field, planning season and constraint type in `field_planning_constraints` (migration
  `0011`), each with an opaque token that is new with every write (a counter would
  restart after a removal, so a stale tab could match a recreated decision). A decision
  that would change an earlier current one is
  refused with `409 replacement_needs_confirmation`, and nothing in the request is saved.
  The assistant then shows the earlier and the new decision, and only the farmer's
  «Replace» resends it, naming the exact earlier decision's token (`replaces`). An approval
  of a decision that changed, or was removed and recreated, meanwhile is refused; the field
  row lock serializes decisions. Removal (`DELETE …/{type}?boundary_revision=…&token=…`)
  likewise deletes only the exact decision shown, for the current boundary. Closing the
  assistant or moving to another field context cancels an open replacement question.
  «Keep the earlier decision» or a cancellation reports `cancelled` to the widget, which
  offers the choice again instead of showing a failure. They are planning preferences, not
  measurements, and farm defaults are never changed. A boundary change makes earlier
  decisions stale. The API adds the current ones to the field context it
  builds and signs, so a browser's context or history cannot mark a constraint confirmed.
  That context carries structured values only (source `farmer_ui`, or `agent_inferred`
  with its confidence): the farmer's quoted words and the free-text machinery note stay
  in the farm record, like scouting notes. Analytics rotation diagnostics list the decisions, with
  those words, for review and withdrawal.
  No rotation comparison or simulation uses them yet. The browser offers
  `compare.set_constraints` to the widget only; the signed agent capabilities
  (`signing.py` `HOST_ACTIONS`) are unchanged.
- The profile uses the existing single-farm-per-deployment architecture, with local
  shared-workspace or authenticated team access. The host signature binds a team turn
  to the server-authenticated farm/member; the browser cannot choose that principal.
  Local mode uses an explicit shared-workspace principal. Authentication does not
  make the deployment a multi-farm tenant service. The remote adapter retains its
  concurrency and rate bounds.
- Fields, Strategies, Agent, Analytics and Farm remain the five destinations.
  Legacy `water` and `rotations` host actions open the corresponding Analytics views;
  current `strategies` and `analytics` action IDs are also signed and validated.
  Selected year/scenario and boundary/run context stay explicit. Another context
  invalidates earlier action references; an aborted response cannot replace current context.
- The widget conversation is still in-memory and restarts across a browser reload.
  The legacy chat endpoint persists genuinely executed replies in its existing DB
  conversation tables, but this integration does not add a widget history drawer or
  persist richer stream/tool transcripts. Do not seed fake conversations to hide that limit.
- API tests validate cross-service payloads against exported JSON schemas. Agent tests
  retain their installed strict Pydantic checks. Both installed services verify the same
  deterministic test-only signed-body/token vectors independently; no sibling source
  files or import paths are injected. The API JSON-schema dependency is development-only.
- Migration `0016_merge_agent_planning` joins the published `0015_merge_onboarding`
  and `0012_merge_agent_constraints` heads without changing either history. Upgrade and
  restore verification must run against disposable PostgreSQL before publication.

See [runtime reference notes](../services/agent/REFERENCE.md) for the agent engine and
[field analytics](field-analytics.md) for how the underlying observations are made.

## Field notifications and guardian mascot

The header inbox and chat use the original guardian WebP from the supplied mobile
demo. CSS handles its short greeting, notification sway and pending-answer motion;
reduced-motion disables these effects. No canvas render loop or animation library
is required. The shared component lives in `apps/web/src/shared/GuardianMascot.tsx`.

`apps/web/src/features/notifications` reads saved water forecasts and compact
Sentinel-2 metadata. It applies the same current water-stress and NDVI inspection
rules used by the field views. Clear, stale, missing, demo or mismatched evidence
does not create an alert; incomplete coverage is shown separately. The inbox shows
each signal's field, observation/forecast date, source and measurement units.

One reader polls every five minutes while the application is visible, with a
one-minute focus cooldown, at most three simultaneous requests, a 15-second
timeout per request and cancellation on a changed boundary/planting footprint.
It never starts analysis jobs, fetches satellite cells or invokes a language model.
These are in-app notifications, not background browser push notifications.

Reading a signal records a bounded, farm/season-scoped identifier in this browser's
local storage. It is not synchronized between devices. Changed boundaries or
new observation dates create new identifiers. Opening the assistant preserves the
selected field/year/scenario and clears historical run context without sending a question. Direct analysis links open that field's water forecast
at the first stress day or its NDVI layer. The inbox is a keyboard-accessible desktop
dialog and a mobile bottom sheet.
