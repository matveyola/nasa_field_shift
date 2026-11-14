# Field Shift integration contracts — v0.1 (draft for team review)

Owner: member 3 (AI agent). Status: **draft, waiting for review by members 1, 2, 4 and 5.**

These contracts are the shared language between the geo pipeline, the simulation engine, the agent, and the frontend. If your service sends or receives one of these shapes, it must validate against the schema.

> **M5:** the envelopes for calling the geo and simulation services (capabilities, field profile,
> comparisons with result ids and expiry) are a separate, **proposed** contract that wraps the M0 types
> below without changing them: `contracts/integration.py`, `contracts/schema/fieldshift-integration.schema.json`,
> spec in `docs/M5_TEAM_INTEGRATION_CONTRACT.md`. Not yet agreed with members 1 and 2.

## Files

| File | What it is |
|---|---|
| `contracts/models.py` | **Source of truth** (Pydantic v2). Edit here only. |
| `contracts/schema/fieldshift-contracts.schema.json` | Generated JSON Schema (2020-12). All types are in `$defs`. |
| `contracts/ts/index.d.ts` | Generated TypeScript types for the frontend. |
| `contracts/fixtures/*.json` | Synthetic examples that validate. Use them to build against before real services exist. |
| `tests/test_contracts.py` | Fixtures validate; 24 invalid samples are rejected; the schema file is up to date. |

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run python -m contracts.export
```

```bash
uv run python -m contracts.export --check
```

```bash
npm install
```

```bash
npm run gen:ts
```

```bash
npm run check:ts
```

After changing `models.py`, run the export, then `gen:ts`, and commit all three files together.

## Conventions

- **Unknown keys are rejected**, and types are not coerced (`"0.21"` is not a number). Every service fails loudly on contract drift.
- **Ids** match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`. Crop ids match `^[a-z][a-z0-9_]{1,63}$` (for example `winter_wheat`). The canonical crop list is owned by member 2.
- **Coordinates**: WGS84, `[lon, lat]`, GeoJSON order. Polygon rings must be closed.
- **Time**: `retrieved_at` is RFC 3339 **UTC** (`...Z`). Ranges and series points are calendar dates (`YYYY-MM-DD`), inclusive.
- **Units**: UCUM codes from a fixed list (`Unit` in `models.py`): `1, %, [pH], mm, mm/d, m3/m3, Cel, Cel.d, d, m, cm, ha, deg, kg/ha, t/ha, g/kg, cmol/kg, MJ/m2/d, m/s`. Need another one? Add it to the list in a PR.
- **Labels**: data carries i18n keys (`fact.soil_ph`), never display text. The frontend translates keys and formats numbers per locale (`uk` uses a decimal comma).
- **Locale**: `uk` or `en`. Requests may send `auto`.

## Provenance: every number says where it came from

Every `Fact`, `Score`, `Timeseries`, layer, and metric carries a `Provenance`.

| `kind` | Meaning | Required fields |
|---|---|---|
| `satellite_product` | A published EO or reanalysis product, as distributed (SMAP L4, IMERG, HLS, NASA POWER, SoilGrids) | `source` with `provider`, `product_id`, `version` |
| `ml_estimate` | Output of a Field Shift ML model (downscaled moisture, yield risk) | `model` with `name`, `version`; add `validation` when you have it |
| `deterministic_calc` | A formula applied to other values (FAO-56 ET₀, GDD, depletion) | `method` |
| `reference` | A value taken from a document or table | `source` |
| `farmer_input` | Stated, clicked, or photographed by the farmer | — |
| `llm_explanation` | Prose from the language model | — and it can **never** carry a number |

`data_status` is independent of `kind`: `live`, `cached`, `fixture`, or `unavailable`. The UI must badge `fixture` data visibly. An `unavailable` fact keeps its `source` but has no value, so the agent can say "SoilGrids has no data cached for this point" instead of guessing.

Unknown product versions are written as `"unverified"`, never guessed. If an `ml_estimate` has no `validation`, the UI and the agent must say that the model is not validated.

## Rules the schema enforces (beyond types)

1. A satellite product or reference value without `source.version` is rejected.
2. An ML estimate without `model` is rejected; a calculation without `method` is rejected.
3. A numeric fact whose provenance is `llm_explanation` is rejected.
4. A fact needs exactly one of `quantity` or `category`, unless it is `unavailable` (then it has neither).
5. A constraint from `farmer_text` or `farmer_voice` needs the `evidence` span it came from. An `agent_inferred` constraint needs `confidence`.
6. `FieldContext` cross-references must resolve: active layers ∈ `available_layers`, chart metrics ∈ `timeseries_index`, `chart_focus` ∈ `available_charts`. Ids are unique.
7. Rotation scores must be `deterministic_calc` or `ml_estimate`. `reason_fact_ids` must exist in `facts`. Ranks are unique.
8. An `AgentMessage` that shows any fixture fact must carry a `fixture_data` warning.
9. Citation URLs must be http(s). Values must be finite (no NaN or Infinity).

## Types and who owns them

### `FieldContext` — owner: member 5 (host app)

The shared state for the selected field. The host keeps it in its store and sends a snapshot with every agent request.

- `revision` increases on **every** change. UI actions carry the revision they were made for; the host **drops actions whose revision is stale** (for example, the farmer switched fields mid-answer).
- `facts` hold compact values only. Long series stay out of the context: `timeseries_index` lists what can be fetched.
- `available_layers` and `available_charts` are the only ids the agent may reference.

### `FieldProfile`, `TimeseriesQuery`, `Timeseries` — owner: member 1 (geo)

`FieldProfile` is what the geo service returns for a field: `facts`, `timeseries_index`, `available_layers`. The host merges it into `FieldContext`. `Timeseries.points` is a list of `[date, value | null]` in strictly increasing date order; `null` marks a gap.

### `CompareRotationsRequest`, `RotationComparison` — owner: member 2 (simulation)

The request carries the farmer constraints and a scenario; the service ignores constraints with `status: "rejected"`. **The constraint → weight mapping is deterministic code in the simulation service**, never the LLM. The response reports the weights it applied in `constraint_weights_applied`.

`ScenarioId`: `typical`, `drought_2024`, `heat_plus_2_5c` (placeholders for member 2 to confirm), and since 2026-11-12 `driest_recent_year` / `hottest_recent_year`: the driest / hottest season of the service's own recent weather archive for that field, replayed as it was (what the Marsden service computes). They are not `drought_2024` or `+2.5 °C`, so they are separate ids; a farmer's "drought" may be answered by either dry id the service offers, under its own name.

`RotationScores`: `water_risk` (%) is required; `soil_cover_days` (d), `n_input` (kg/ha), `yield_risk` (%, the chance of a loss beyond a threshold) and `yield_loss_water` (%, the size of the harvest lost to water shortage) are optional. A service sends only what it computes: an absent score means "not computed", never zero. Each score's `provenance.method` says what exactly it is (for example "share of crop water need not met").

### `Constraint` — owner: member 3, consumed by members 2 and 5

A union discriminated by `type`. Each type has a typed `value`:

| `type` | `value` |
|---|---|
| `irrigation_available` | boolean |
| `fertilizer_budget` | `minimize_n` \| `low` \| `normal` |
| `erosion_protection` | boolean |
| `rotation_length_years` | integer 2–6 |
| `excluded_crops`, `preferred_crops`, `market_preference` | crop id list |
| `previous_crop` | crop id |
| `organic_only` | boolean |
| `machinery_limit` | free text; display only, never scored |

`status` is `proposed`, `confirmed`, or `rejected`. Pills in the UI let the farmer confirm or remove each one.

### `UIActionEnvelope` / `UIAction` — produced by the agent, applied by members 4 and 5

The widget passes envelopes to the host through `onAction(envelope)`. The widget never imports map or chart code.

| `action.type` | Payload |
|---|---|
| `map.set_layers` | `layer_ids`, `mode: replace \| add` |
| `map.focus_field` | — |
| `time.set_range` | `start`, `end` |
| `chart.focus` | `chart_id`, `metric_ids`, optional `highlight` range |
| `compare.set_constraints` | `constraints` |
| `compare.select_rotations` | `rotation_ids` |
| `scenario.set` | `scenario` |
| `panel.open` | `panel: soil_portrait \| comparison \| calendar \| citations`, optional `ref_id` |

`trigger: "auto"` means the host applies the action immediately; `"chip"` means it is shown as a button. **The host must re-check every id against its live store before applying.** The agent service checks them too, but the host is the last line of defence.

### `AgentRequest`, `AgentEvent`, `AgentMessage`, `ErrorEnvelope` — owner: member 3

`POST /v1/agent/chat` takes an `AgentRequest` and answers with Server-Sent Events. Each frame is:

```
event: <AgentEvent.event>
data: <AgentEvent as JSON>
```

The order is `turn.started`, then any mix of `tool.started` / `tool.finished` / `text.delta`, then exactly one `turn.completed` (carrying the full `AgentMessage`) or `error`. **Render the final state from `turn.completed`**; deltas are for the typing effect only.

In `AgentMessage`, every number shown in `blocks` is present in `facts` with its provenance, and every `[n]` marker matches a `citations[].id`. Citation URLs come from corpus metadata, never from model text.

**Answer blocks (added 2026-11-10, additive).** Besides `text` and `clarifying_question`, `blocks` may hold presentation blocks the agent shows under its text:

| `type` | Shows | Fields |
|---|---|---|
| `metrics` | 1–6 values of the field as cards | `title?`, `fact_ids` |
| `table` | options (rows) on the same measures (columns) | `title?`, `columns`, `rows[{label, fact_ids}]` (one id or `null` per column) |
| `bar_chart` | one measure, one unit, across 2–8 options | `title`, `bars[{label, fact_id}]` |
| `callout` | one short caution (`warning`) or notice (`info`) | `tone`, `text`, `fact_ids` (≤ 3) |

They never carry a number of their own: values are ids of `facts` in the same message (the contract refuses a block that names a missing fact, a bar chart with mixed units, or a table row of the wrong width), and labels are plain words without numbers (checked by the agent server). Blocks with rotation results carry `scenario`, set by the agent server from the verified result (never by the model) and never mixed within one block; show it with them. A client built before this change rejects these messages at validation: update agent and widget together.

**App screens and what the app can do (added 2026-11-10, additive).** The widget now sends `client.capabilities` = `{actions, screens}`: the action types the host applies (its `HostCapabilities.actions`) and its own screens `[{screen_id, label, how_to_open?}]`, where `how_to_open` is plain words for finding the screen by hand ("Меню → Погода"). This is UI data, never permission. With it the agent proposes only those action types, opens a screen with the new view-only action `app.open_screen {screen_id}` (may be `auto`), and when the app cannot show something it names a screen and quotes its `how_to_open` instead of inventing menus. Without it (an older widget) the agent behaves as before. `UIState.screen_id` is the screen on display; the host updates it (and the revision) when it opens a screen. After the agent changed the view automatically (layers, period, chart, screen), the widget offers **"Повернути як було"**: it sends the inverse actions from the view before the answer (`origin: "farmer_click"`), and only while nothing else has changed since.

**Upgrade order.** Both sides check messages strictly (unknown fields are refused), so a new widget talking to an old agent server is refused ("field data not accepted"), and an old widget refuses new answer blocks. Deploy the agent server first, then the widget, and keep them on the same version.

Non-2xx HTTP responses carry an `ErrorEnvelope`. `message_key` is an i18n key; `retryable` tells the widget whether to offer "try again".

## Fixtures

| File | Shows |
|---|---|
| `field_context.poltava.json` | Full context (uk): satellite, ML, calculated facts; layers; charts; two constraints |
| `field_context.kansas.json` | Smaller context (en); a categorical fact; degree-days |
| `field_context.mykolaiv.json` | Point geometry, an **unavailable** fact, a UI-sourced and an agent-inferred constraint, drought scenario |
| `rotation_comparison.poltava.json` | Two rotations with deterministic and ML scores, a reference fact |
| `timeseries.poltava.sm_rootzone.json` | Monthly series with a gap |
| `agent_request.kansas.json` | A full chat request (embeds the Kansas context) |
| `agent_message.poltava.json` | An answer with facts, a citation, constraints, an auto action and two chips |
| `agent_events.poltava.json` | The SSE stream for that answer |

**Every value in these files is synthetic.** All of them are marked `data_status: "fixture"` (or `unavailable`), and a test fails if any fixture claims `live` or `cached` data. The citation quote is a placeholder, not a quote from FAO-56.

## Using the schema from JavaScript

The schema is JSON Schema 2020-12. With Ajv, use `ajv/dist/2020`, add `ajv-formats`, and register the `tsType` keyword. It is a hint for the TypeScript generator, not a validation rule:

```js
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
const ajv = new Ajv2020({ strict: true });
addFormats(ajv);
ajv.addKeyword("tsType");
```

Ajv covers the structural rules. The cross-field rules (1–9 above) run in Python; the frontend relies on the agent service having enforced them, plus its own id re-check for UI actions.

## Versioning

`schema_version` follows semver. An additive, optional field is a minor change. Renaming, removing, or tightening anything is a breaking change: bump the version and announce it to the team before merging.

## Changes from the TASK-01 draft

- Tool inputs that never leave the agent service (`search_knowledge`, `propose_constraints`, `propose_ui_actions`, `analyze_soil_photo` / `SoilPhotoObservation`) are **not** in this package. They are agent-internal and will be defined in M2, M3 and M7. Everything here crosses a team boundary.
- Added `FieldProfile` (geo → host), `TimeseriesQuery`, and `CompareRotationsRequest`, so members 1 and 2 have request shapes too.
- Added the `rotation` fact domain, for facts that explain rotation scores.
- Export command is `python -m contracts.export`, not `scripts/export_schemas.py`.

## Open questions for teammates

- **Member 1:** Can your service return `FieldProfile` and `Timeseries` in this shape? What are the real `layer_id`s and `metric_id`s? Which product versions can you confirm (NASA POWER, HYSOGs are `unverified` in fixtures)?
- **Member 2:** Confirm the score set (`water_risk`, `soil_cover_days`, `n_input`, `yield_risk`) and their units, the scenario ids, the crop id list, and ownership of the constraint → weight mapping.
- **Member 4:** Confirm the `chart_id`s and that every `UIAction` can be applied.
- **Member 5:** Confirm `FieldContext` ownership, `revision` semantics, and the `onAction` handler.
