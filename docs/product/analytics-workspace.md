# Field Analytics

Select a field and a UTC period of 1–92 days. The default starts today and
covers 30 days. Requests use the selected field and its current boundary
revision. Old values clear when the field, boundary or applied dates change.
Rotation and crop decisions open in Strategies. Existing water links can open a
specific forecast date or a validated historical water run.

## Weather sources

| Data | Source and granularity |
| --- | --- |
| Historical precipitation, air temperature and ET₀ | ERA5 reanalysis via Open-Meteo, daily from 1940, subject to a seven-day release lag |
| Historical surface soil moisture | ERA5-Land reanalysis, daily mean volumetric moisture at 0–7 cm, in m³/m³ |
| Operational weather | Open-Meteo daily precipitation, air temperature extrema and reference ET₀, up to 16 days including today |
| Climate scenario | EC-Earth3P-HR HighResMIP via Open-Meteo for future dates missing compatible operational values. Displays simulated precipitation and temperature as means of intervals of up to seven days |

Charts list source spans next to missing intervals. The rain chart uses mm/day
and the metric card shows totals per source span in mm. The air-temperature
chart uses °C. The climate scenario uses one model and a high-emissions
forcing. It provides a planning perspective outside the operational horizon.
Historical data, ET₀, satellite products and soil moisture keep their own
source boundaries.

The owned-field API is
`GET /api/fields/{field_id}/weather-dashboard?geometry_revision=…&start=…&end=…`.
The response groups each metric into chronological spans with source or gap
reason and dated points. Revision mismatches return HTTP 409. The adapter
validates UTC, units and physical ranges, applies a bounded provider timeout,
and caches provider responses in 15-minute buckets.

## Water and drought

The root-zone forecast is a physical FAO-56 water balance in mm using the
recorded crop and a reference soil profile. The chart names the rooting depth
and reference threshold. The water budget shows the covered dates and sums
modeled inputs, losses and net change over those dates. A saved historical
run linked by ID may supply the water budget for its own dates.

The warning uses modeled daily root-zone reserve below the model's threshold.
It lists intervals of modeled deficit. The current warning checks the next
30 days. A clear message requires all 30 consecutive days and sufficient
model applicability. The available root-zone source currently provides a
shorter operational run, so a lack of signal normally shows partial coverage.
The selected-period warning states its own dates. Missing, stale and inapplicable
forecasts have separate statuses. Details report the model's freeze, thaw and
dormancy limitations. Climate scenario rows do not contribute to this check.

## Verification

Run the API weather dashboard, existing field-weather and water-forecast tests
and Ruff. Run the web unit tests, TypeScript and production build, followed by
the desktop and mobile workspace Playwright suite. The workspace
`analytics-dashboard.spec.ts` uses synthetic provider replies to check the
production route. The isolated legacy Analytics fixture still exercises
existing rotation, soil, vegetation and water components.
