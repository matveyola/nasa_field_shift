# Польовий помічник (Field Assistant) — assistant widget (M4)

A React + TypeScript widget for the Field Shift agent service: a floating launcher, an accessible
conversation panel, streamed answers, verified FAO-56 citations, farmer-condition chips and
typed map/chart action proposals. It speaks only the M0 contracts (`../contracts`) and the
existing M1 endpoints. It owns no map, chart or simulation code: it proposes, the host applies.

```
frontend/
  src/widget/               the package (public API: src/widget/index.ts)
    contracts/              M0 types (re-exported) + generated standalone validators
    transport/              fetch + SSE parser + client (streamTurn, sendMessage, checkBackend), errors
    state/                  conversation reducer (one thread per field) and the turn lifecycle hook
    actions/policy.ts       action safety rules: shared by the widget and host adapters
    i18n/                   uk/en dictionaries, number and constraint formatting
    ui/                     launcher, panel, messages, citations, chips (presentation only)
    styles/widget.css       design tokens + component styles, all under .fsa-root
  src/demo/                 demo host: fixture fields, illustrative map, host action adapter
  e2e/                      Playwright: replayed real SSE (fixtures/) + opt-in live spec
  scripts/gen-validators.mjs  JSON Schema → standalone validators (no eval at runtime)
```

## Run the demo with the real backend

Four terminals, from `field-shift-agent/`:

```bash
uv run python -m integration_stub --port 8100
```

```bash
AGENT_AGENT=deterministic AGENT_PORT=8000 uv run python -m agent_service
```

```bash
AGENT_AGENT=scripted-llm AGENT_PORT=8001 AGENT_GEO_PROVIDER=http AGENT_GEO_URL=http://127.0.0.1:8100/geo AGENT_SIM_PROVIDER=http AGENT_SIM_URL=http://127.0.0.1:8100/sim uv run python -m agent_service
```

```bash
npm --prefix frontend install && npm --prefix frontend run dev
```

Open http://localhost:5173. Vite proxies `/agent` → :8000, `/agent-llm` → :8001 and `/integration` →
:8100 (override with `FS_AGENT_URL` / `FS_AGENT_LLM_URL` / `FS_INTEGRATION_URL`), so the browser talks to
one origin, as it would to a production backend-for-frontend. The demo opens as a blank white page
with only the assistant (the host logic, field and map state still run underneath); `?host=full` shows
the white demo host page with the map, field facts and action log, and `?host=dark` the original dark
host (`src/demo/demo.css`). http://localhost:5173/phone.html previews the blank page inside a generic
phone frame at a current iPhone's CSS size (393 × 852), with language and demo-mode switches; it is a
preview, not an emulator (no Safari toolbars or touch keyboard). Pick the service in the host page's top
bar (`?host=full`) or with `?backend=rules|scripted|signed`:

| Demo "Agent service" | Backend | Shows |
|---|---|---|
| Rules (M1) | `AGENT_AGENT=deterministic` | field facts with provenance, condition proposals, map actions, "simulation not connected" |
| Scripted LLM + FAO-56 | `AGENT_AGENT=scripted-llm` (+ built knowledge index, + stub) | the real M2 loop, `search_knowledge` and server-verified M3 citations, and (M5) `compare_rotations` against the contract stub, played by a scripted fake model |

M5 host flow in the demo (`src/demo/services.ts`, `hostActions.ts`, `ComparisonCard.tsx`): on field
selection the host POSTs `/integration/geo/v1/field-profile`, validates the response with the generated
contract validator, checks the field id and merges facts / layers / timeseries into its `FieldContext`
(revision bumped only if the data really changed). It probes `/integration/sim/v1/capabilities`; only
then does it declare `simulation: true` and accept `compare.select_rotations`. When the farmer clicks the
agent's "open comparison" chip (`panel.open`, `ref_id: <result_id>`), the host GETs
`/integration/sim/v1/comparisons/{id}`, validates it, checks field, status and expiry, and only then shows
the comparison table (values, units, uncertainty, kind, fixture labels, engine, timestamps, result id).
Unknown, expired or foreign ids are refused and logged.

Neither is an AI model, and the widget says so (`/health/ready` reports the agent). No API key is
needed. With `AGENT_AGENT=anthropic` the same widget talks to Claude (not verified yet: no
credentials). All demo data is the M0 synthetic fixtures and is badged as such. The map is an
illustrative stand-in that draws no data values.

## Mount it in the host app

```tsx
import { FieldShiftAssistant, actionProblem, type ActionRequest, type HostCapabilities } from "@field-shift/agent-widget";

const capabilities: HostCapabilities = {
  actions: ["map.set_layers", "map.focus_field", "chart.focus", "compare.set_constraints", "scenario.set"],
  simulation: false, // true once member 2's comparison service is wired in
};

function onAction(req: ActionRequest) {
  const ctx = store.getFieldContext(); // the LIVE context, not a render-time copy
  const problem = actionProblem(req.envelope, ctx, capabilities, req.fieldId);
  if (problem) return { status: "rejected", reason: problem } as const;
  const next = applyToMapChartsOrComparison(req.envelope.action); // your code; bumps revision
  return { status: "applied", revision: next.revision } as const; // report the new revision
}

<FieldShiftAssistant
  transport={{ baseUrl: "/api/agent" }}      // same-origin path on YOUR backend (see Security)
  fieldContext={fieldContext}                 // M0 FieldContext or null
  fieldName="Poltava · north field"           // FieldContext has only an id
  locale={locale}                             // "uk" | "en"
  capabilities={capabilities}
  onAction={onAction}
  placement={{ side: "right", offsetX: 24, offsetY: 88 }} // clear of the map's own controls
/>;
```

`actionProblem` takes an optional 5th argument: the simulation results the host has fetched and checked
(`Map<result_id, {result_id, field_id, field_context_revision?, expires_at, rotation_ids}>`); a result computed
for a revision the host has not reached is refused (`stale_context`), and an expired one (`expired`). Comparison-panel and
rotation-selection actions are refused unless they reference a live result for the current field.

| Prop | Purpose |
|---|---|
| `transport` | `baseUrl`; optional `fetch` (e.g. one that adds the app session) and `headers()` (CSRF token, short-lived user token); `deadlineMs` (150 s), `stallMs` (45 s) |
| `fieldContext` | The M0 `FieldContext` snapshot. Every change must bump `revision` (CONTRACTS.md) |
| `locale` | UI language and the request's `locale` |
| `capabilities` | Which action types the host can apply now, and whether a simulation engine exists. Unsupported actions are shown as unavailable; suggestions adapt |
| `onAction` | Applies one action. Return `{status:"applied", revision}` or `{status:"rejected", reason}` |
| `placement`, `theme` | Corner, offsets, z-index; any `--fsa-*` token override |
| `trustedSourceHosts` | Hosts allowed in "View original source" (default `www.fao.org`, `fao.org`; https only) |
| `container` | Portal target (default `document.body`, so transformed map containers cannot clip it) |
| `sessionId`, `showRevision` | Stable session id; show the context revision (debug) |

Exported helpers: `actionProblem`, `isCurrent`, `requiresClick`, `streamTurn`, `sendMessage`,
`checkBackend`, `SSEParser`, `AgentError`, `fieldContextProblem`, and every M0 type.

### How actions reach the host

1. The service validates ids and the revision before an action leaves it (`ui_action_problem`).
2. The widget shows each envelope. `trigger: "auto"` display actions (e.g. show the SMAP layer)
   are applied at once; everything else waits for a click. `compare.set_constraints`,
   `scenario.set` and `compare.select_rotations` **always** wait for a click, whatever `trigger` says.
3. Before calling `onAction` the widget checks the field id and the revision chain: the revision
   the answer was made for, plus the revisions produced by applying that same answer's actions
   (so "show SMAP" does not make the answer's other chips stale). Any other change (the farmer
   toggled a layer, switched fields) makes the chip stale: it is refused and says "ask again".
4. The host re-checks with `actionProblem` against its live store: schema, field, revision,
   capability, layer/chart/metric ids, and that constraints are farmer-confirmed.
5. The widget shows the outcome: applied (or "applied automatically"), rejected with the reason,
   "not available in this app yet", or, for a scenario, "Scenario selected. No simulation has
   been run."

Farmer conditions: the agent's `constraints_proposed` render as dashed "Proposed" chips with
Confirm / Reject / Hide. Nothing reaches the host until the farmer clicks Confirm or Reject,
which send `compare.set_constraints` with that constraint as `status: "confirmed"` or
`status: "rejected"` for the host to save. Hide only hides the chip in this conversation: nothing
is sent or saved. A chip shows "Confirmed" or "Rejected by you · saved" only when the host's
`FieldContext.constraints` holds that decision; until then it shows "Saving…". The agent's own
actions never confirm or reject. If the decision would replace an earlier one, the host asks the
farmer to approve the replacement separately; if the farmer keeps the earlier decision, the host
returns `cancelled` and the chip offers the choice again.

Edit (before deciding): opens an editor suited to the type (yes/no, fertiliser budget, 2–6 years,
crops, previous crop, machinery text). Editing saves nothing; the farmer still confirms or rejects.
A changed value becomes `source: "farmer_ui"` with no quoted evidence or confidence; an unchanged one
keeps the proposal's source and words. Crops come only from the host's `planningCrops` (crops whose
identity the host shares with the agent); a proposal naming any other crop cannot be edited and
can still be decided as proposed, never with a crop dropped or swapped. A decision the host already
held before this answer reads "Already confirmed/rejected earlier · nothing new saved".

## Streaming and the turn lifecycle

`streamTurn` POSTs to `/v1/agent/stream` with `fetch()` (EventSource cannot POST) and parses SSE
incrementally (`transport/sse.ts`): split UTF-8, CRLF/CR/LF, multi-line `data`, comments and
keep-alives, ids. Every frame is checked against the M0 `AgentEvent` schema with generated
standalone Ajv validators (no `new Function`: works under a strict CSP). Bad JSON, unknown
events, extra fields, an `event:` name that disagrees with the payload, or anything before
`turn.started` ends the turn as a protocol error. Reading stops at the first `turn.completed` or
`error`, so a duplicated terminal is never seen; each turn has exactly one outcome.

`text.delta` is shown as a muted draft only while streaming (in LLM mode the service sends it
after grounding). The validated `turn.completed` message replaces it; a failed turn removes the
draft and says so. Tool steps appear only when `tool.started` arrives ("Searching FAO-56
guidance…"), with an elapsed-seconds counter. After 15 s of search the panel says honestly that
search is slow on this server (CPU reranking measured 33.5 s in M3). Nothing shows a fake
percentage.

A turn is aborted when the farmer presses Stop, closes the panel, switches fields, or the widget
unmounts. Late results of an aborted turn are dropped, and its row in the old field's thread
says why. Timeouts: 150 s overall, and 45 s without a byte (the service pings every 15 s).
Retries happen only when the farmer clicks "Try again", and only for retryable errors.

History: completed turns of the current field's thread only (≤ 10 entries, ≤ 16 000 chars each,
≤ 24 000 chars in total, citation markers stripped). Conversations live in memory, one per field,
and are never written to storage.

## Security

- **No secrets in the browser.** `AGENT_API_KEY` is a server-to-server secret: never put it in
  `transport.headers`, Vite env, storage or the bundle. In production, put the agent service
  behind the app's own backend-for-frontend (BFF):

  ```
  browser ──(session cookie + CSRF, same origin /api/agent/*)──▶ app backend
            authenticates the user, rate-limits, adds Authorization: Bearer $AGENT_API_KEY
            ──▶ agent service /v1/agent/{message,stream}, streaming the SSE body through unbuffered
  ```

  The demo's Vite proxy has the same shape, without auth (development only).
- **Untrusted text.** Answers and source excerpts are rendered by a small Markdown subset
  (`ui/Markdown.tsx`) into React text nodes: no `dangerouslySetInnerHTML`, no raw HTML, and no
  links from prose at all.
- **Links.** Only `Citation.url` from the validated message is linked, and only if it is https,
  on an allowlisted host, with no credentials (`ui/safeUrl.ts`). Links open with
  `noopener noreferrer` and `referrerpolicy="no-referrer"`.
- **Actions.** Only schema-valid, typed envelopes from the final message become buttons; text
  inside an answer or a source can never become an action. Host checks are described above.

## Design

Monochrome soft UI (the owner's references, 2026-11-12): a light grey screen `#F2F2F2` with white cards
(20 px radius, soft shadows) for the field, the agent's steps, figures, tables, conditions, actions,
evidence and sources; black pill buttons with a slight sheen for the main action; round white icon
buttons; black for what is chosen or important (the farmer's bubble, a confirmed condition, an applied
action, a warning card); big figures in the display cut with a small unit. Greys only: ink `#0D0D0D`,
muted `#5C5C5C`, faint `#6E6E6E` (AA on the screen and on white), sunken `#EBEBEB`; made-up data is a
dashed grey tag; the one hue is the red dot of a lost connection. Tokens live at the top of
`styles/widget.css` (`--fsa-*`). Hosts theme through the semantic tokens (`--fsa-bg`, `--fsa-bg-raised`,
`--fsa-text`, `--fsa-accent`, `--fsa-on-accent`, `--fsa-line`, ...); every black surface follows
`--fsa-accent`. Names from earlier palettes still steer their counterparts: `--fsa-emerald` → accent,
`--fsa-mint` → accent text. Type: Inter Variable with its optical-size axis (the demo bundles it; hosts
without it fall back to the system grotesk); `--fsa-font-display` lets a host set a separate display face.
The composer keeps its earlier shape, sizes and Onest type at the owner's request, pinned by its own token
values at the start of its section; its pill is grey-white, its focus ring and send button black, to match.
Contrast is checked with axe in a real browser (no violations).

The agent's name is the `agentName` prop (default "Польовий помічник", in English "Field Assistant"; chosen by the
owner 2026-11-12 for plain words over a brand): it is the panel title, the
dialog's accessible name and part of the launcher's label. The mark is a field parcel with crop rows,
white on a black disc (`ui/icons.tsx` `FieldMark`); while the assistant works a thin arc turns around it. The composer's paperclip is shown but announced as unavailable
(`aria-disabled`, "not supported yet"): files are not part of the agent contract. The microphone
dictates into the message box with the browser's own speech recognition (Web Speech API, in the
widget's language); nothing is sent until the farmer presses send. In Chrome and Edge the browser
vendor's service does the recognition, which the button's label says; where the API is missing the
button is labelled as unsupported, and a refused microphone is explained just above the composer.

The launcher is a 60 px black disc with the mark; while the panel is open it shows a chevron.
Two grey rings pulse once each time it appears (about 4.8 s, so no endless motion), animating only
`transform` and `opacity`. Opening, the panel unfolds from the disc: a `clip-path` circle centred on
the launcher grows over 460 ms (a CSS transition, so closing mid-way folds back from where it is), and
the content settles from the composer up to the header. Closing folds it back into the disc in 200 ms
(`CLOSE_MS`). `clip-path` never changes the panel's box. Under `prefers-reduced-motion` the panel
cross-fades in 140 ms, the rings are not rendered and every other change is instant. Desktop: a
non-modal panel (`role="dialog"`, no `aria-modal`; the map stays usable), 412 × ≤ 720 px. Phones
(≤ 640 px): a full-screen modal sheet (`aria-modal="true"`, Tab kept inside, page scroll locked,
`100dvh` + safe-area insets, 16 px input text so iOS does not zoom). Design review screenshots:
`FS_DESIGN_SHOTS=1 npx playwright test e2e/design.spec.ts` → `docs/screenshots/design/`.

## Accessibility (WCAG 2.2 AA target)

Keyboard: launcher → Enter opens, focus goes to the input; Enter sends, Shift+Enter adds a new
line; Escape closes a source, then the panel; focus returns to the launcher or citation marker.
While a source is open the conversation underneath is `inert`. Citation markers are buttons named
"Source 1: <title>"; source-list rows are named "Open source 1: <title>" (label in name).
Disclosures are native `<details>`. Progress is announced through one polite live region (tool
started, answer ready, stopped); errors use `role="alert"`. Focus rings are a 2 px green ring with a
white gap (the composer shows a 1.5 px green edge plus a halo). The microphone is a toggle
(`aria-pressed`). Touch targets are ≥ 34–44 px
(citation markers grow to 28 × 24 on coarse pointers). Forced-colours mode keeps borders, the dashed
"proposed" edge and the action arrows. Checked by axe-core in jsdom (structure) and in a real browser
(`e2e/assistant.spec.ts`, including contrast).

## Tests

```bash
npm --prefix frontend test
```

```bash
npm --prefix frontend run test:e2e
```

```bash
npm --prefix frontend run typecheck
```

```bash
npm --prefix frontend run check:validators
```

| Suite | Covers |
|---|---|
| `transport/sse.test.ts` | chunk boundaries, split UTF-8 (2- and 4-byte), several frames per chunk, multi-line data, comments, CRLF/CR/LF incl. split CRLF, ids, unterminated frame |
| `transport/client.test.ts` | normal turn, byte-by-byte chunks with keep-alives, invalid JSON, unknown event, extra field, name/payload mismatch, out-of-order events, duplicate terminal, error event, HTTP errors (ErrorEnvelope and proxy pages), offline, unexpected end, abort reason, stall and deadline timeouts, invalid request never sent, host headers |
| `actions/policy.test.ts` | host checks (field, revision, capability, ids, unconfirmed constraints, malformed envelopes), click-required rules, revision chain, confirmation envelope |
| `state/conversation.test.ts` | per-field threads, draft → final reconciliation, exactly one outcome, history limits |
| `ui/Markdown.test.tsx` | no HTML injection, no links, citation markers only for real citations; URL allowlist |
| `FieldShiftAssistant.test.tsx` | launcher states, keyboard, Escape and focus return, pulse vs reduced motion, phone modal + focus trap, no-field state, Ukrainian copy, valid AgentRequest, tool activity, citation detail and blocked URLs, constraint confirmation, dismiss, map chip, stale chip, field switch mid-answer, close aborts, offline + retry, axe |
| `e2e/assistant.spec.ts` (Edge/Chromium) | the 10 required flows with replayed real SSE, plus simulation refusal, field switch, offline, reduced motion, axe with real colours |
| `e2e/integration.spec.ts` | M5: profile from the geo service, synthetic comparison explained and opened by result id, unknown result refused, simulation down, field switch hides the comparison |
| `e2e/live.spec.ts` (`FS_E2E_LIVE=1`) | the same flows against the running backends and the contract stub; writes `docs/screenshots/` |

The e2e fixtures are real responses of the M1 rules agent and of the M2/M3 scripted loop
(`e2e/record_fixtures.py`). They contain two short FAO-56 quotes (attributed, under 300
characters), as any citation does. `PW_CHANNEL` picks the browser (default: system Edge on
Windows).

## Known limitations

- **No field, no question.** M0 `AgentRequest.field_context` is required, so the widget cannot
  send general FAO questions without a selected field. It explains this instead of inventing a
  context. Making the context optional is a contract change for member 3 / M5.
- **No live LLM.** Verified with the rules agent and the scripted LLM loop only; the Anthropic
  path is still untested (no credentials).
- **Retrieval quality in the demo.** The scripted backend runs lexical retrieval (hybrid needs about 2 GB more
  RAM than this machine had free), so Ukrainian FAO questions can surface weak passages
  (M3 benchmark: uk Hit@5 0.625 lexical vs 0.833 hybrid). The citations are still real and verified.
- **Labels.** `label_key` translations exist only for the fixture keys; unknown keys fall back to
  readable words. The host should own the label catalogue.
- **Clarifying-question options** are sent as plain replies; there is no structured answer type in M0.
- **Bundle.** Widget ≈ 57 kB gzip (unminified ES build) + 5 kB CSS, React excluded; the
  generated validators are about half of it.
- Screen-reader behaviour was checked with axe and by reading the accessibility tree, not with
  NVDA/VoiceOver by a person.
