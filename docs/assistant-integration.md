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
- Map and chart chips point to the same selected field. The API allows only supported
  navigation actions from the signed context. The UI checks the boundary revision
  before opening a chip. Existing conversations are preserved by migration `0005`.
- Rotation simulations remain available in the Rotations tab. The agent's
  proposed simulation contract and external geo connector are disabled in this
  profile. The agent cannot start a comparison or save a farmer's constraints.
- The profile uses the existing single-farm local deployment. The process limits
  the remote adapter to two simultaneous turns and ten per minute. Before exposing
  the app to multiple farms, add farmer authentication and field authorization in
  the host API. The internal request signature identifies the host, not a farmer.

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
field and signal type, clears historical run context and prepares an editable
question without sending it. Direct analysis links open that field's water forecast
at the first stress day or its NDVI layer. The inbox is a keyboard-accessible desktop
dialog and a mobile bottom sheet.
