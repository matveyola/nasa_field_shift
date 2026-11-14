# Soil moisture training observations

The training target is the daily volumetric water content in the topsoil, measured by a probe at about 5 cm and expressed in m³/m³. Each row represents one station and UTC day. The predictor uses a regional 0–7 cm layer, so comparison with the observations measures agreement with individual probes at comparable depth. Fine-scale differences across a field and moisture in the root zone require separate validation.

| Network | Source | Target preparation |
| --- | --- | --- |
| NOAA U.S. Climate Reference Network | [Hourly02 archive](https://www.ncei.noaa.gov/pub/data/uscrn/products/hourly02/) and [station catalog](https://www.ncei.noaa.gov/pub/data/uscrn/products/stations.tsv) | `SOIL_MOISTURE_5` in m³/m³. Shift interval-ending timestamps back one hour before assigning a UTC day. Require the concurrent 5 cm soil temperature to be above freezing. |
| USDA NRCS Soil Climate Analysis Network | [SCAN program](https://www.nrcs.usda.gov/resources/data-and-reports/soil-climate-analysis-network) and [Air and Water Database REST API](https://wcc.sc.egov.usda.gov/awdbRestApi/swagger-ui/index.html) | Hourly ordinal 1 `SMS` at −2 inches. Keep records flagged valid (`V`), convert percent to m³/m³ and convert station standard time to UTC using the catalog's `dataTimeZone`. |

Both networks are publicly accessible without a key. The NOAA observations are public data under the [NCEI Open Data Policy](https://www.ncei.noaa.gov/sites/default/files/2023-12/NCEI%20PD-10-2-02%20-%20Open%20Data%20Policy%20Signed.pdf). Attribute USCRN to NOAA NCEI and cite [Diamond et al. 2013](https://doi.org/10.1175/BAMS-D-12-00170.1) and [Bell et al. 2013](https://doi.org/10.1175/JHM-D-12-0146.1). Attribute SCAN observations to USDA NRCS National Water and Climate Center. The extracted SCAN stations are all operated by NRCS. The download uses published federal observations and the project retains source attribution. [US Government works](https://www.usa.gov/government-works)

Values must be finite and above zero through 0.8 m³/m³. A daily mean requires at least 20 distinct hourly records and coverage in each six-hour quarter of the day. The aggregation refuses conflicting values for the same hour. Missing or rejected observations remain absent. The acquisition manifest records the extraction years, networks and states, each source URL and its content digest, and the CSV's digest.

## Reproduce the station snapshot

Run from `packages/field-shift-ml` after `uv sync --locked --python 3.12`. Use a data directory outside source control. The raw cache can require several hundred megabytes.

```sh
uv run --locked python run.py fetch_observations \
  --start-year 2021 --end-year 2025 \
  --cache-dir /path/to/model-data/observations-raw \
  --output /path/to/model-data/observations.csv
```

Reuse the raw cache to reproduce an earlier snapshot. Use `--refresh` to re-download the source objects, including any corrections published since the previous snapshot. For a narrower extraction, select `--network scan` or `--network uscrn`, `--states`, or `--growing-season-only` for April through October. The output includes station identity, date, coordinates, volumetric moisture, state, network, location, and the number of accepted samples.

Keep all rows for an evaluation station out of fitting and tuning. In the Midwest, the public SCAN catalog identifies Ames in Iowa, the University of Nebraska's [Rogers Memorial Farm](https://bse.unl.edu/research-facilities/facilities/rogers-memorial-farm/), and the Ohio State [Molly Caren Agricultural Center](https://si.osu.edu/sites/default/files/SustainabilityCommunityEngagementVisioningPaperFINALda.pdf). These are point readings at agricultural monitoring sites. The evaluated locations, year, coverage and performance relative to ERA5-Land are recorded in the trained model's validation report. The reference probes are at separate sites from the demonstration field near Bondurant.

## Independent Iowa field measurements

The Iowa Flood Center's HydroShare release provides 15-minute water content at 5 cm. The independent extraction covers `CROPCNTRL01`, `WILSONFRM01` and `WILSONFRM02`. The 2025 USDA NASS Cropland Data Layer at the three probe coordinates classifies the first as corn and both Wilson Farm probes as soybeans. The Wilson Farm sites represent nearby fields in the same coarse region. [HydroShare dataset](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/) · [USDA 2025 crop layer](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_CDL25_FGDC-STD-001-1998.htm)

```sh
uv run --locked python run.py fetch_observations \
  --network ifc --start-year 2025 --end-year 2025 \
  --cache-dir /path/to/model-data/observations-raw \
  --output /path/to/model-data/observations-ifc.csv
```

Keep this CSV separate from model fitting and tuning. The parser uses the explicit offsets in the CSV to assign each quarter-hour to UTC, discards off-grid timestamps, averages hours with at least three plausible measurements, and applies the same daily coverage requirement as the training observations. The Iowa measurements are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Cite Marcela Rojas (2026), *Iowa Flood Center - Hydrologic Monitoring Network (Hydrostations)*, HydroShare. Field Shift changes the source data by applying filters and aggregating observations. [Published dataset](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/)

## Sealed v2 farm holdout

The v2 farm cohort uses eight different IFC stations with 5 cm probes. Selection used metadata before target acquisition. An eligible location had to be at least 40 km from every v1 NOAA/SCAN input station and prior IFC evaluation probe. Its exact coordinates also had to fall on a 2025 corn or soybean class in the 10 m USDA Cropland Data Layer. The eight selected stations are at least 33.67 km from one another. The manifest fixes station IDs, coordinates, land cover classes, and the 2025 UTC evaluation period. The observation extractor verifies the station coordinates against that manifest. [Sealed site manifest](../packages/field-shift-ml/src/field_shift_ml/evaluations/iowa-v2-farm-sites.json) · [USDA CDL service](https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer) · [IFC data](https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/)

Run from `packages/field-shift-ml`.

```sh
uv run --locked python run.py fetch_observations --network ifc-v2 --start-year 2025 --end-year 2025 \
  --cache-dir data/observations-raw --output data/observations-ifc-v2.csv
```

## USDA SSURGO soil survey features

Training and inference use one USDA Soil Data Access extractor. It intersects each coordinate with an SSURGO map unit and selects the highest-percentage component with a horizon covering 5 cm. Stable map-unit, component and horizon IDs break ties. Sand, silt, clay and organic matter are percentages. Bulk density is in g/cm³. Water content at 33 and 1,500 kPa serves as field-capacity and wilting-point proxies, respectively, in m³/m³. Null survey properties stay null. Each profile retains map-unit and component IDs, horizon depths, drainage class and survey save date. A probe or field centroid samples a survey map unit that can contain multiple soils. [USDA Soil Survey Geographic Database](https://www.nrcs.usda.gov/resources/data-and-reports/soil-survey-geographic-database-ssurgo) · [SDA column descriptions](https://sdmdataaccess.nrcs.usda.gov/documents/TableColumnDescriptionsReport.pdf)

For F-03 the live survey returned Clarion loam, map unit `2877332`, with 51% sand, 31% silt, 18% clay and 2.5% organic matter in the selected 0–15 cm horizon. Its field-capacity and wilting-point proxies are 0.267 and 0.119 m³/m³. These properties describe the soil survey map unit at the queried point. [USDA Soil Data Access](https://sdmdataaccess.sc.egov.usda.gov/WebServiceHelp.aspx)

```sh
uv run --locked python run.py fetch_soil_profiles --stations data/observations.csv \
  --stations data/observations-ifc.csv --stations data/observations-ifc-v2.csv \
  --cache data/ssurgo-cache --output data/soil-profiles-v2.json
```

The acquisition caches validated results by coordinate and records the snapshot SHA-256. Keep the cache with the training inputs to reproduce the same soil survey vintage. A fresh survey snapshot can reflect subsequent USDA updates. Runtime uses a bounded in-process cache with daily expiry.
