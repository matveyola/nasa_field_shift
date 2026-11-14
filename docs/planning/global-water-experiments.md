# Global season and water experiments

This checkpoint expands runnable calculations, not scientific validation. The
production planning API still defaults to AquaCrop-OSPy; the new layered-water
experiment has no automatic production promotion or crop-yield output.

## Runnable crop coverage

| Crop | Pinned OSPy generic yield baseline | Explicit-calendar layered water |
| --- | --- | --- |
| Maize | Yes | Experimental |
| Wheat | Same-year, no snow/frost | Experimental cross-year calendars |
| Barley | Same-year, no snow/frost | Experimental |
| Soy | Yes; exact upstream `Soybean` identity | Experimental |
| Sunflower | Yes; exact upstream `Sunflower` identity | Experimental |
| Rapeseed | No upstream profile in this adapter | Experimental supplied parameters |
| Peas | No upstream profile in this adapter | Experimental supplied parameters |
| Rice | Unsupported | Paddy unsupported |

None of these cells establishes independently validated global yield accuracy.
Generic crop coefficients cannot be substituted for a local cultivar calibration.
A registered crop and a mathematically executable water scenario are distinct.

## Continuous water experiment

`field_shift_science.layered_water` adapts the existing team balance, soil and ET0
core from Ruslan's source SHA468a03d. Original author, file hashes, uncertain rights,
changes and limitations are preserved in packaged NOTICE/source receipts. The
Iowa-specific crop calendar, mutable normal-temperature calibration and synthetic
weather generator are not imported.

Callers provide dated sowing/harvest, four positive phase lengths, explicit
dormancy windows, crop Kc/root/depletion parameters and provenance. Phase lengths
sum to inclusive active calendar days. Calendars can cross New Year in either
hemisphere. A shared soil/snow state continues through successive crops and fallow;
overlapping simultaneous crops are rejected. Soil is explicit aligned10cm layers
with FC/WP/saturation/opening VWC and drainage. Runoff coefficients, initial residue,
opening snow, seven preceding temperatures, snow threshold, melt and freeze assumptions are mandatory. No default50% start,
Iowa winter dates, silent root clipping or missing-weather interpolation is used.

Input precipitation is **total liquid-equivalent precipitation**, including snow.
Do not reinterpret planning-v1 `liquid_rain_mm` as that quantity. The winter
experiment uses its own explicit forcing input; production winter integration
requires an agreed precipitation/snow contract and additional verification.

The model preserves daily soil+snow water mass to1e-6mm in the tested cases. It
retains the upstream approximations: opening-water stress before today's rain,
no sublimation under snow, degree-day snow/freeze assumptions, simple drainage and
curve-number runoff. It does not model winter survival, photoperiod/genetics,
nitrogen, paddy, flood depth, groundwater or erosion. Passing conservation tests
proves accounting, not predictive accuracy.

Run an explicit experiment from any working directory with the installed models
package (JSON paths are caller-controlled):

```bash
python -m field_shift_models.global_water --input /path/request.json --output /path/report.json
```

The command compares at most3alternatives and8weather members, preserving source
identities, footprint restrictions and identical scenario windows. Its ranking is
water-deficit ordering only. Yield and gross margin are null; it is never a profit
recommendation. Price/cost assumptions must remain in the owning economic planner.

## Future season scenarios

`field_shift_models.seasonal.historical_scenarios` maps complete historical weather
members into a future window up to3years. All fields share each member's original
years, preserving observed spatial co-variation. Source dates, checksums and issue
availability remain recorded. Future observations, duplicate/gapped days, impossible
leap-day starts and partial members fail. Leap-day differences use a continuous
source-day sequence, not independent month-by-month resampling.

Members have no probability weights or calibrated predictive intervals. They are
historical replay scenarios under stationarity, not exact three-year weather
forecasts. Native issued medium-range/seasonal forecast products require their own
lead/region skill evaluation. The research comparison is in
[global model selection](global-model-selection.md).

## Six-location execution evidence

[Global cases](../../packages/field-shift-models/reports/global-execution-cases-v1.json)
use actual NASA POWER2023–2024 UTC daily temperature, precipitation, solar radiation,
RH and2mwind at representative Kyiv, Rio, Singapore, Sahara, Antarctic and LaPaz
coordinates. ET0 is derived with the declared FAO-56 method, meanRH approximation
and NASA representative elevation. POWER is coarse gridded forcing, not a field
sensor. Raw responses and SHA receipts remain outside Git.

Crop/soil/calendar parameters are deliberately controlled assumptions; equal crop
parameters isolate climate differences and cannot rank crop species. City points
are not classified farm parcels. Antarctic ice abstains for every crop; Sahara and
high-altitude cases abstain without confirmed arable land; paddy rice is unsupported.
Only source-backed land assessments can establish real farm applicability.

Reproduce with the public responses described in the data-source registry:

```bash
python -m field_shift_models.global_case_benchmark --data-dir /path/public-data --output /tmp/global-cases.json
```

These six execution cases are separate from empirical yield metrics and from the
existing G01–G24 agronomic/contract suite. No synthetic geography success is counted
as measured accuracy.
