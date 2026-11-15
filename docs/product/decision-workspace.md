# Field Shift decision workspace

The product helps a farmer decide what to sow before procurement, compare seasonal tradeoffs and understand when to revisit a decision. The map remains the field workspace; the assistant remains the central navigation action. Simplicity means a clear order of information, not removal of agronomic evidence.

## Five destinations

- Fields: map, current field/zone, concise evidence-backed conclusion and next action. A bottom sheet previews the business decision and opens strategy comparison, relevant analytics or the contextual assistant. Boundary, zoning and variable-rate tools remain available beneath the decision layer.
- Strategies: nearest planting opportunities and a selectable planning period, supported objectives, actual alternatives, why/why not, commitments and human-confirmed application/replanning. Allocation supports one to six harvest years and seven crops. The installed rotation engine compares two or three plans with one to three explicitly dated crop cycles from its 13-profile catalog; sourced calendar and management support further limit actual execution. Native planning retains its own five-crop and provider-specific limits. A harvest year is not an independently modeled crop cycle. Show the selected field and planning period before inputs; put detailed assumptions and evidence behind disclosure. No manufactured alternatives when the server returns fewer than three.
- Agent: keep the existing teammate-built assistant, tools, grounding and signed field context. Open it from the center navigation button and from decisions without replacing it with a decorative chat screen.
- Analytics: one territory workspace with Overview, Water, Vegetation, Rotations and Soil sections. Lead with what the data imply and how current/complete they are; charts, history, source metadata and advanced operations follow. Future crop choices belong in Strategies.
- Farm: fields, resources, defaults, observations and team settings. Reuse existing behavior and maintain field/season/scenario context when moving between destinations.

Old Water/Rotations/planning/vegetation URLs and agent/notification actions must resolve to their new destination while preserving field, season, scenario, trajectory and relevant job/date context. Browser back/forward and direct links must agree with the visible section.

## Evidence and content

The positioning notes are product direction, not measurements. Their illustrative profit, match, flood, hybrid and logistics percentages must never become production data. Existing objectives map to reference scenario margin, worst tested margin and modeled water demand; label their actual meaning. Gross margin is not net profit and tested-scenario spread is not a calibrated probability interval.

Use saved backend/model results, source-aware operational observations and explicitly labeled user assumptions. Keep missing/stale/unsupported/infeasible states actionable; do not replace absent predictions with zero or fabricate goal cards. Where the trusted planning pipeline is not configured, retain the existing reference-scenario planner as an explicitly bounded workflow rather than silently presenting it as independently validated forecasting.

The decision brief summarizes supported conclusions, the two most immediate checks and a next action using the current draft. The weather label comes from the actual dated source segments: a future calculation using only historical analogues is not labeled as a live forecast. Country-level yield or price references remain benchmarks, and missing included costs remain missing margin.

Analytics uses a single scrollable section bar with reachable 44px controls. Strategy workflow controls use compact pressed-state buttons that respond to actual container width and enlarged text; their workspaces stay mounted so switching views preserves drafts. Saved assistant conversations are read-only historical records with their stored analysis year and scenario; opening them preserves the active widget draft. They are not replayed as current field evidence. New widget conversations remain in memory and are not automatically added to the service's saved-conversation list.

Analytics puts the returned 24-hour temperature range, five-day rainfall and modeled root-zone reserve before the completed 7/30-day archive. Five days describes this short-term rainfall view; it is not a seasonal forecast or a model-wide limit. Water threshold dates, missing coverage and cold/reference-model applicability stay visible. Hourly tables, water arithmetic and saved seasonal scenarios remain in named disclosures, with dates and units.

Crop artwork is colored SVG at source and grayscale in the current UI. The twelve botanical drawings cover thirteen current handoff profiles, the seven allocation crop labels and recorded rice; wheat profiles share their botanical drawing. This coverage is visual identity, not an extension of scientific crop support: the rice icon does not enable rice or paddy modeling. Unknown labels retain the generic crop.

Primary screens use one clear heading, a contextual conclusion, a focused primary action and concise comparison. Use differentiated layouts (decision cards, season timeline, compact summaries, tables/charts only where they answer a question); avoid repeated KPI grids. Keep detailed evidence and technical identities accessible below the conclusion. Support keyboard access, Ukrainian/English, narrow screens, phone preview and desktop without horizontal overflow or hidden controls.

## Delivery verification

Verify real navigation/deep links, context retention, assisted actions, unknown/stale data, confirmed apply/replan and all inherited field/zone operations. Preserve backend/wire contracts and release gates. Use directly affected tests and TypeScript/build checks, then the fast/full app boundary and real mobile/desktop browser checks on an isolated local stack before publishing the jobs branch. Update the local preview with the reviewed build so the user can inspect the actual redesign.
