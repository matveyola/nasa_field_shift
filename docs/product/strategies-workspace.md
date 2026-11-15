# Strategies workspace

Strategies supports crop decisions before procurement. The selected field stays in focus, while a farm allocation can include multiple registered fields. Changing the field or inspecting another season keeps the allocation draft intact. The explicit first-season control starts a different planning period.

A planning year means the harvest year. Winter sowing can occur in the preceding calendar year. Allocation selects one crop per field per year; multiple crop cycles within a year are not represented by this contract.

## Two supported workflows

**Crop allocation** uses registered field areas, crop history and the server's crop catalog. A new allocation starts with one harvest year; the user can select up to six planning years, ending no later than 2100. All catalog crops are visible. Enabling a crop makes it an option, not a claim of local suitability. Preferences, crop areas, return breaks, botanical-family rules and locked decisions remain editable beneath disclosure. The one-season workflow permits one crop; an explicit requirement for two different crops per field must be turned off before a one-season calculation. The interface does not silently relax it.

The solver's saved assignments supply the next-season decision cards, field names and reasons. The season sequence and crop areas show the full requested period. A feasible result at the search limit stays labeled as such. Missing assignments stay unknown; unavailable or infeasible results do not expose an apply action. Allocation is not a yield, climate-risk or financial forecast. Crops and hybrids outside this catalog are not invented.

**Scenario comparison** supports one, two or three modeled harvest years within the model's forcing and input bounds. Its objectives retain their actual definitions: reference-scenario margin, worst tested margin and modeled water demand. Cards show only returned alternatives and stored metrics, with farm objective units separated from field-level yield and irrigation. Gross margin is not net profit; scenario spread is not a calibrated probability. Rule-based exclusions are scoped to the inspected field snapshot, rulebook and season. Detailed economic assumptions, field profiles, source identities and model evidence remain accessible below disclosure.

Sowing-window disclosure shows saved assignment dates where available, identifies their rulebook and labels them as calendar constraints. It does not claim a weather-optimized date. Allocation has no sowing calendar and explicitly leaves timing undetermined, with the required inputs and access to water analytics and the assistant.

Creating a field profile does not provide missing trusted weather, regional rules or model parameters. When these inputs are unavailable, scenario comparison explains the gap and offers input review and refresh. Crop allocation remains usable independently. The assistant and relevant analytics are available from the decision workspace.

## Editing and confirmation

Saved allocations restore their own duration and constraints, including older three-season plans. Explicit horizon edits invalidate the old allocation result without discarding the editor's other choices. Locks prevent shrinking beyond a committed season. The modeled workflow removes editable assumptions outside a newly selected horizon and explains this behavior before selection; commitments remain protected.

Generating an allocation saves a draft. Applying it remains a separate human-confirmed operation. Retry requests reuse the existing idempotency identity. Modeled application retains the confirmation checkbox, stale-input checks and the commitment-refresh gate before replanning. Switching between workflows does not discard draft state. Root navigation owns persistence while visiting another destination.

## Verification scope

The isolated Strategies browser harness uses explicitly synthetic API responses to verify behavior on desktop and narrow screens: allocation without trusted model configuration, outages, retry identity, saved plans, confirmation, field context, Ukrainian copy, empty catalogs and horizon changes. Model tests verify saved-assignment summaries, actual objective units, missing values, scoped exclusions and one/two-season payloads. The existing planning browser suite covers geographic presentation, source evidence, keyboard navigation, stale application and apply/replan recovery. These checks verify interface behavior; they do not establish forecast accuracy. Real navigation and live backend verification are performed on the combined application.
