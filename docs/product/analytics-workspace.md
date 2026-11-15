# Territory Analytics

Analytics brings Overview, Water, Vegetation, Rotations and Soil into one field workspace. One field, season and scenario control bar applies across its sections. The app owns URL routing and context callbacks; Analytics never changes another context dimension when a user changes one selector. The assistant opens with the shared context. A completed water run has a separate contextual assistant action carrying its validated job ID.

Overview orders recorded conclusions: a current water threshold crossing, incomplete field/soil inputs, repeated crop records, and satellite availability. It uses the existing current-water policy and satellite quality filter. Missing, expired, other-season and other-boundary evidence cannot produce a healthy-state conclusion. These are inspection and data-completion prompts, not calibrated risk probabilities or crop recommendations.

Water retains the actual forecast, linked forecast day, historical simulations, saved jobs, scenario comparison, daily water balance, budget and recovery controls. Live forecasts are explicitly separated from the selected historical season. Demo water remains an explicit opt-in source and is never used for overview conclusions. Embedded mode removes the legacy heading and duplicate field/scenario selectors. Changing the season resets historical period inputs and prevents an old-season result appearing as current.

Vegetation retains NDVI chart/date selection, comparison, CSV, spectral indices, within-field variability, cross-season history and precision operations. Chart selections do not leave the page. Explicit map actions transfer the season, chosen dates and index via `MapFocus`. Terrain and seasonal weather have their own disclosures instead of being repeated in every section. Missing imagery, old observations, assumed historical boundaries and source limitations stay visible. NDVI alone is not a crop diagnosis or yield loss.

Rotation history comes from the saved field profile, includes only records before the selected season and distinguishes confirmed, reported and unknown crops. An intervening gap never establishes consecutive cropping. Future crop choice and application belong in Strategies. Saved sequence diagnostics retain historical comparison and yield-model provenance, but embedded Analytics has no farm planning form, sequence editor or main-plan mutation. Demo stress charts are suppressed until a historical comparison exists; reference sequences remain labeled.

Soil shows recorded depth intervals, quality, hydraulic inputs and optional texture/chemistry. Recorded volumetric moisture (m³/m³) and available reserve (mm) retain their own depth/time/source information. Profile snapshots with an outdated geometry revision are withheld and direct users to refresh in Strategies. Source records and missing-data reasons are accessible. Synthetic profiles are labeled before their content. Terrain shows surface-gradient evidence and its limitations; it is not a flood probability.

The legacy Water, Rotations and FieldAnalysisPanel entry points retain default props. The new Analytics export uses the shared `AnalyticsPageProps` contract. Root routing must wire it to the five-destination navigation; this component lane alone does not replace the application routes.

## Verification

Run focused pure-model checks with Node24:

```sh
node --disable-warning=ExperimentalWarning --test apps/web/tests/analytics/*.test.ts
```

The isolated browser fixture harness exercises desktop and touch-mobile context retention, missing profiles, Ukrainian copy, no duplicate headings/selectors, date/index map handoffs, separation from future planning and historical job/assistant continuity:

```sh
node services/agent/frontend/node_modules/@playwright/test/cli.js test --config apps/web/tests/analytics/playwright.config.ts
```

It uses port18103 and the existing locked app/widget dependencies. API responses are synthetic presentation fixtures in these component tests. They are not an empirical model evaluation or a substitute for the integrated real-backend desktop/mobile checks. No production fixture data, model weights, new dependencies or backend contracts were added.
