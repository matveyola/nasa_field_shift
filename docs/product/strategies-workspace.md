# Strategies workspace

Strategies supports crop decisions before procurement. The selected field stays in focus, while a farm allocation can include multiple registered fields. Changing the field or inspecting another season keeps the allocation draft intact. Changing the explicit first-harvest-year control or following a calendar idea starts a different allocation period. Edited or selected unapplied drafts require confirmation before that replacement.

A planning year means the harvest year. Winter sowing can occur in the preceding calendar year. Allocation selects one crop per field per year; multiple crop cycles within a year are not represented by this contract.

## Calendar opportunities first

The default entry is the selected field's calendar, independent of model or financial readiness. The planning date defaults to the browser's local calendar day. A rolling one-to-six-year lookahead starts on that date; it is separate from the allocation's harvest-year count. Date bounds mirror the endpoint: from 1900, ending by 2100. Selecting a historical date does not reconstruct historical field state.

The UI consumes `plantingOpportunitiesApi.get` and the sourced fields endpoint. It separates reviewed windows open on the planning date, the current regional reference month, upcoming entries, passed entries and missing calendar data. A typical regional month is displayed at month precision, never as first/last-day planting permission. A reviewed day window retains its actual endpoints. Options retain the returned crop, cycle, system, maturity basis and source identity. Groups expose counts and an explicit show-all action after six entries. Passed and unavailable entries remain inspectable beneath disclosure.

Each card distinguishes calendar reference from eligibility, weather assessment and profit. No profitability, yield, weather probability or scientific optimality is inferred. Source details include version, availability, license, reference year, resolution, location and checksum when supplied. Missing inputs have readable labels; the source's reasons and limitations remain available. A known no-data cell retains its source and reasons, while a crop with no calendar is shown as unknown. Crop-model parameter availability is not field validation.

A supported current or upcoming entry with a known supported harvest year can open allocation for that maturity year. It does not lock a crop or invent a sowing date. An ineligible, passed, unsupported or unknown-year entry cannot create this handoff. Changing the period requires confirmation when allocation edits or an unapplied draft are present. Cancel or Escape preserves them; same-year handoffs preserve the current editor. If the new year would take the current allocation duration beyond 2100, the dialog explicitly offers a one-year replacement rather than silently clipping. Pending generation, opening or application blocks replacement. Saved plans remain stored.

Responses are scoped to field, boundary revision, planning date and lookahead. In-flight obsolete requests are aborted; mismatched responses are rejected. Loading, failure/retry, empty results and missing sources do not turn into invented crop decisions. Field changes clear a pending handoff while preserving the existing farm draft. Allocation and scenario panes stay mounted when switching workflows.

## Allocation and modeled scenarios

**Crop allocation** uses registered field areas, crop history and the server's crop catalog. A new allocation starts with one harvest year; the user can select up to six planning years, ending no later than 2100. All catalog crops are visible. Enabling a crop makes it an option, not a claim of local suitability. Preferences, crop areas, return breaks, botanical-family rules and locked decisions remain editable beneath disclosure. The one-season workflow permits one crop; an explicit requirement for two different crops per field must be turned off before a one-season calculation. The interface does not silently relax it.

The solver's saved assignments supply the next-season decision cards, field names and reasons. The season sequence and crop areas show the full requested period. A feasible result at the search limit stays labeled as such. Missing assignments stay unknown; unavailable or infeasible results do not expose an apply action. Allocation is not a yield, climate-risk or financial forecast. Crops and hybrids outside this catalog are not invented.

**Scenario comparison** supports one, two or three modeled harvest years within the model's forcing and input bounds. Its objectives retain their actual definitions: reference-scenario margin, worst tested margin and modeled water demand. Cards show only returned alternatives and stored metrics, with farm objective units separated from field-level yield and irrigation. Gross margin is not net profit; scenario spread is not a calibrated probability. Rule-based exclusions are scoped to the inspected field snapshot, rulebook and season. Detailed economic assumptions, field profiles, source identities and model evidence remain accessible below disclosure.

Sowing-window disclosure shows saved assignment dates where available, identifies their rulebook and labels them as calendar constraints. It does not claim a weather-optimized date. Saved allocation assignments themselves have no sowing calendar and explicitly leave timing undetermined, with the required inputs and access to water analytics and the assistant.

Creating a field profile does not provide missing trusted weather, regional rules or model parameters. When these inputs are unavailable, scenario comparison explains the gap and offers input review and refresh. Crop allocation remains usable independently. The assistant and relevant analytics are available from the decision workspace.

## Editing and confirmation

Saved allocations restore their own duration and constraints, including older three-season plans. Explicit horizon edits invalidate the old allocation result without discarding the editor's other choices. Locks prevent shrinking beyond a committed season. The modeled workflow removes editable assumptions outside a newly selected horizon and explains this behavior before selection; commitments remain protected.

Generating an allocation saves a draft. Applying it remains a separate human-confirmed operation. Retry requests reuse the existing idempotency identity. Modeled application retains the confirmation checkbox, stale-input checks and the commitment-refresh gate before replanning. Switching between workflows does not discard draft state. Root navigation owns persistence while visiting another destination.

## Verification scope

The isolated Strategies browser harness uses explicitly synthetic API responses to verify behavior on desktop and narrow screens: allocation without trusted model configuration, outages, retry identity, saved plans, confirmation, field context, Ukrainian copy, empty catalogs and horizon changes. Calendar tests cover day/month precision, all status groups, missing maturity, crop support, rolling-period bounds, late responses, field scope, retry, dirty-draft confirmation and in-flight mutation protection on desktop and touch mobile. Pure tests also cover local date construction and malformed source dates. Model tests verify saved-assignment summaries, actual objective units, missing values, scoped exclusions and one/two-season payloads. The existing planning browser suite covers geographic presentation, source evidence, keyboard navigation, stale application and apply/replan recovery. These checks verify interface behavior; they do not establish forecast accuracy. Real navigation and live backend verification are performed on the combined application.
