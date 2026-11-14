# Field analytics

The Fields panel has Overview and Vegetation & terrain views. Seasonal NDVI uses
persisted Sentinel-2 observations, with one usable scene per calendar month.
The chart's band is the observed P10–P90 spatial range, not a prediction interval.
Missing months stay disconnected. Selecting a chart date changes its numeric
readout; the variation map explicitly remains the latest usable scene.

The map shows relative NDVI classes, clipped to the planted area when specified.
Low/high classes differ from that footprint's median by 0.15 NDVI. The percentage
uses valid observed pixels, not the whole field. Clouds and missing pixels remain
transparent. An inspection cue requires at least 10% lower-NDVI area and an
observation no older than 30 days; this is a transparent heuristic, not a disease
classifier. Source scene, acquisition date, clear coverage and resolution are
visible. See [processing and refresh](vegetation-analysis.md).

Water includes a cumulative balance for the actual saved forecast or a historical
weather calculation. Rain plus irrigation and crop use plus modelled drainage
accumulate over the selected period. Daily continuity and mass balance are checked
before totals are displayed. Forecast fluxes remain forecasts, historical fluxes
remain model calculations, and scenario demo data is excluded from this panel.
Surface runoff is not part of this bucket balance.

Farm priorities first rank forecast threshold crossings, then current satellite
inspection cues, then fields lacking usable water forecasts. Forecast evidence
requires today's coverage and an initialization no older than three days. An
unknown state never implies low risk. Each action opens the same field and, where
appropriate, the forecast day or vegetation panel. Compact summaries are requested
with at most three concurrent requests per data source.

The template assistant uses saved field evidence in Ukrainian or English. It
cannot manufacture a water forecast when no real run exists. Explicitly linked
historical/demo simulations retain their original source labels and run context.
Satellite and water signals open their corresponding map/chart. Field revisions
and planted-area fingerprints prevent old geometry from describing a changed
field. Missing field links do not silently fall back to another field.

Terrain directions are optional and calculated once per field boundary/model
version. They indicate potential downhill movement on a 30 m surface model, not
measured flow, runoff volume or flood extent. See [terrain method](terrain-directions.md).

The interface uses the existing Google Sans, forest/ivory palette, crop icons and
Heroicons. Desktop uses the resizable Fields panel; mobile uses its existing
expandable sheet, with all analysis controls available inside it.
