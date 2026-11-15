# Water reference coverage

The operational IFS / FAO-56 bucket supports the seven allocation crops: maize,
soy, spring-wheat reference, barley, sunflower, rapeseed/canola and dry/seed peas.
This expands reference parameter coverage, not geographic suitability or validated
crop-yield forecasting. Rice, fresh peas and explicitly winter wheat do not silently
borrow another management profile. Generic Wheat preserves the existing spring
wheat convention; a cultivar-specific or winter soil profile needs its own inputs.

Source: FAO Irrigation and Drainage Paper 56, [Table 12, crop coefficients](https://www.fao.org/4/x0490e/x0490e0b.htm)
and [Table 22, rooting depths and depletion fractions](https://www.fao.org/4/x0490e/x0490e0e.htm).
Parameters are `Kc_mid`, rooting depth in metres and depletion fraction `p`:

| Crop | Kc_mid | Root depth m | p |
| --- | ---: | ---: | ---: |
| Maize | 1.20 | 1.0 | 0.55 |
| Soy | 1.15 | 0.6 | 0.50 |
| Spring wheat reference | 1.15 | 1.0 | 0.55 |
| Barley | 1.15 | 1.0 | 0.55 |
| Sunflower | 1.15 | 0.8 | 0.45 |
| Rapeseed | 1.15 | 1.0 | 0.60 |
| Dry/seed peas | 1.15 | 0.6 | 0.40 |

Existing three-crop parameters are unchanged. The bucket selects the lower
published rooting depth. For sunflower and rapeseed the published Kc range is
1.0–1.15; this fixed-midseason reference selects its upper bound. FAO associates
the lower coefficient with less dense rainfed populations. Local density,
phenology, rooting restrictions and climate adjustments are not calibrated here.
Dry peas deliberately differ from fresh peas' depletion fraction (0.35).

The forecast still discloses reference loam, fixed midseason Kc/rooting depth,
zero irrigation/snowmelt/runoff and grid-based forcing. It reports a physical
reserve in mm, distinct from surface moisture in m³/m³. Expanding the lookup does
not promote experimental maize correction to other crops, does not convert
stress to yield or profit and does not establish an optimal sowing date.
