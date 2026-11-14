# Public data for global preseason planning

Reviewed 2026-11-14. The executed experiment is a US research-plot benchmark,
not evidence of worldwide field-scale yield accuracy. NASA weather covers the
requested global points; observed yield labels do not. No model is promoted.

## Acquired evidence and reproducibility

The first published [yield report](../../packages/field-shift-ml/reports/global-yield-benchmark-v1.json)
uses protocol **ardn-power-preseason-v2**, frozen before inspecting model errors.
The [source receipts](../../packages/field-shift-ml/reports/global-data-receipts-v1.json)
record URLs, SHA-256, units, API headers, coordinates and byte counts. Raw files
are external inputs, never package resources or repository fixtures.

- ARDN/CSCAP has 3,797 finite dry-yield labels: maize 2,300, soybean 1,362,
  wheat 135, in eight US states, 2011–2015. Valid ordered planting/harvest joins
  retain **3,751**: maize 2,268, soybean 1,356, wheat 127. Exclusions: six
  missing/ambiguous sowings, 28 missing/ambiguous harvests, 12 invalid calendar
  orders. Three wheat records additionally cannot use the later issue date.
- These are 29 experiment identifiers, conservatively grouped into **20 physical
  sites** using shared experiment prefixes and coordinates within 0.02 degrees.
  This rule deliberately groups nearby experiments; it is not cadastral proof.
  All experiments at a held-out physical site are excluded from fitting and selection.
- Downloaded 30 metadata locations from NASA POWER, 1981–2015, eight parameters,
  **50,756,179 bytes**. Exact duplicate coordinates are reused. Nearby sites may
  share a much larger native weather grid cell and are not independent weather samples.
  Representative coordinates average available corner pairs; four irregular NAEW
  sites use their single published coordinate pair, not an invented polygon centroid.
- Downloaded **590,660 bytes**, 2023–2024, for Kyiv (50.45,30.52), Rio
  (-22.91,-43.17), Singapore (1.35,103.82), Sahara (23,13), Antarctica (-80,0),
  and La Paz (-16.5,-68.15). Each has 731 valid days for every requested variable.
  These are weather stress inputs without observed crop-yield truth. Antarctica's
  valid weather response does not establish agricultural suitability.
- Existing local audit inputs include ARDN soil, plot, operation and onsite weather
  tables plus the CSCAP workbook. Onsite weather has 54,780 rows; its `merra_srad`
  already shares NASA MERRA lineage. ARDN's dry kg/ha label is divided by 1,000,
  **not moisture-corrected again**. An earlier source audit checked the parent
  workbook's standard-to-dry conversion within 0.11 kg/ha.

For an existing ARDN cache, from an installed `field-shift-ml` environment:

```bash
python -m field_shift_ml.global_data \
  --data-dir "$ARDN_DATA" --cache-dir "$POWER_CACHE" \
  --receipts "$REPORT_DIR/acquisition.json" --global-panel
python -m field_shift_ml.global_yield_benchmark \
  --data-dir "$ARDN_DATA" --cache-dir "$POWER_CACHE" \
  --protocol "$REPORT_DIR/frozen-protocol-v2.json" \
  --output "$REPORT_DIR/benchmark.json"
```

The source directory must contain `ardn-agro.csv`, `ardn-sitemeta.csv`,
`ardn-ops_plant.csv`, and `ardn-ops_harv.csv` (cp1252 source encoding). Acquisition
uses one worker, an 8 MiB response bound and 1 GiB aggregate budget; it reuses
validated files. A reused file has no newly claimed retrieval time. The optional
six-point panel is separate from training. URL query, checksum and scientific
source version are necessary to reproduce a mutable API response; downloading
again years later is not guaranteed to yield identical bytes.

## Frozen benchmark and findings

The original 2011–2013 fit / 2014 selection split was superseded **before error
inspection**: 2014 validation labels can postdate a 2015 winter-wheat decision.
Protocol v2 uses 2011–2012 fitting, 2013 selection, excludes 2014, and tests 2015.
Every fitting/selection label must have observed harvest plus **365 days** no later
than the target issue date. This lag is a conservative assumption, not an observed
publication timestamp. Selection is separate by target crop and issue date;
training uses the eligible pooled crops. Selection labels are never refitted.
Missing selection cohorts produce baseline-only diagnostics, not silent tuning.

Fixed issue dates are February 1 / March 1 for maize and soybean, and July 1 /
August 1 in the previous year for winter wheat. Recorded sowing is used only for
eligibility and reporting lead, never as a predictor. Features are crop, location,
harvest year, monthly 1984–2009 POWER climatology, and 30/90-day weather means
ending seven days before issue. Each feature block requires 95% actual coverage.
No future weather, nitrogen, harvest measurements, assumed initial soil water,
or post-sowing plant population is used. POWER's pre-1984 missing solar values
are excluded from the climatology. Radiation is MJ/m²/day, rain mm/day, temperature
°C, humidity %, wind m/s, pressure kPa. Monthly precipitation features are means
of daily mm/day values, not monthly totals.

Compare the training mean, crop mean, crop-specific linear trend, ridge penalties
1/10/100, and 100-tree gradient boosting with depths 2/3, learning rate 0.05 and
seed 7. Scaling/vectorization fit only training rows. Selection minimizes eligible
2013 target-crop MAE with deterministic ties. Report all fixed baselines and the
selected member of each model family; **do not pick a different winner using test
errors**. Reports contain full fit/selection/test membership, each prediction,
feature/source hashes, bias, MAE, RMSE, R², state/crop results, and physical-site/
year/crop macro errors. Null R² means insufficient or constant targets. A reporting
correction detects identical decimal targets before variance arithmetic, avoiding
spurious enormous negative R² from floating-point rounding. The report was
regenerated with the same frozen protocol, memberships, selected models and predictions.

Temporal 2015 results below use the candidate selected solely on 2013 labels.
All errors are dry t/ha. Plot rows are not independent weather samples.

| Issue / crop | Test n | Validation-selected candidate | MAE | RMSE | R² | Crop-mean MAE |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| Early maize | 482 | GBDT depth 2 | 2.070 | 2.441 | 0.511 | 3.349 |
| Early soybean | 267 | GBDT depth 3 | 0.747 | 1.021 | -0.128 | 0.886 |
| Early wheat | 25 | Ridge alpha 1 | 2.235 | 2.543 | -8.959 | 1.199 |
| Standard maize | 482 | Ridge alpha 1 | 2.357 | 2.873 | 0.323 | 3.349 |
| Standard soybean | 267 | GBDT depth 3 | 0.816 | 1.094 | -0.296 | 0.886 |
| Standard wheat | 25 | GBDT depth 2 | 1.453 | 1.658 | -3.236 | 1.199 |

**Wheat fails to improve on its crop mean; do not replace the selected candidate
with a more favorable test baseline and call that model selection.** Early wheat
selection has only eight eligible rows, versus 27 for the standard issue.

Spatial results expose weak transfer. Holding Wisconsin out of both fitting and
selection, early maize GBDT MAE is 2.686 vs crop mean 3.826 (24 labels), but soybean
is worse, 1.010 vs 0.673 (16 labels). Holding KELLOGG out, early maize GBDT is worse,
3.175 vs 1.696 (12 labels). There is no KELLOGG wheat test cohort. The early temporal
maize site-year macro MAE is 2.159, rather than its plot-row MAE 2.070. Other group
metrics and all failures remain in the report.

This is a **retrospective predictor diagnostic**, not an operational hindcast:
ARDN was published after these seasons; historical POWER vintages are not archived
here; the development selection step is not a replay of model issuance in 2013.
The public test years were previously inspected for source readiness and are not
sealed. No confidence interval or global performance claim is justified by this
small regional panel. Observations of the planted crop cannot identify the yield
of an unplanted alternative: crop-choice regret is unavailable. A future SOTA
comparison must use the same target basis, spatial unit, cutoff, held-out years,
and geography. County/admin benchmarks and plot trials are different tasks.

## Source registry

Read the version-specific source terms before redistribution. Earthdata is a
catalog/access system, not a single scientific dataset. Grid resolution is not
field measurement accuracy. Soil moisture in m³/m³ and root-zone water storage in
mm require separate contracts and declared depth/integration assumptions.

| Source / official reference | Coverage, units and timing | Access / terms / correct use |
| --- | --- | --- |
| [NASA POWER daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) and [meteorology lineage](https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/) | Global daily meteorology from 1981; native MERRA-2 0.5° latitude × 0.625° longitude, solar has different lineage/resolution. Explicit UTC vs local solar time. Recorded API v2.10.3; MERRA2/SRB/SYN1DEG/POWER headers. Low-latency products and later revisions differ. | Public authless API; NASA open scientific data, acknowledge POWER and underlying sources. Read response units/fill/quality. Valuable for humidity/wind/solar and historical scenarios, not independent corroboration of existing MERRA-derived inputs. |
| [SMAP SPL4SMGP v8](https://nsidc.org/data/spl4smgp/versions/8) | Global approximately 9 km, three-hourly, from March 2015; modeled/assimilated surface and root-zone moisture, m³/m³. Surface/root-zone depths are distinct. | Free Earthdata account normally required. Cite product/version/DOI. Official page flags geolocation issues for 2026-05-14 through 2026-07-28: quarantine affected data until corrected provenance is verified. Not direct field truth; no 2011–2014 coverage. |
| [ERA5-Land](https://cds.climate.copernicus.eu/stac-browser/collections/reanalysis-era5-land) | Global land, hourly, 1950 onward; distributed 0.1° grid, about 9 km native. K temperature; m precipitation; volumetric soil moisture in layers 0–7/7–28/28–100/100–289 cm. | Copernicus, not NASA. Free CDS account/token and license acceptance; current catalog CC BY 4.0. Reanalysis driven by ERA5, not a direct soil sensor. Record accumulation conventions and product revision. |
| [SoilGrids 2.0](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html), [ISRIC overview](https://isric.org/explore/soilgrids) | Global 250 m predicted soil properties, uncertainty quantiles, six depth intervals from 0 to 200 cm. Scale factors differ by property; e.g. pH stored ×10. | CC BY 4.0; public raster access. Use depth-weighted distributions and record version. Hydraulic parameters inferred by a pedotransfer function remain assumptions, not measured profiles. |
| [NASA SRTM guide](https://www.earthdata.nasa.gov/s3fs-public/2025-05/SRTM_Quick_Guide.pdf) | SRTMGL1 v3 approximately 30 m, static February 2000 elevation in m; roughly 56°S to 60°N, not all polar terrain. | NASA open data; Earthdata access may require account. Useful terrain/slope covariates, not soil depth. Choose a separately licensed global DEM for polar coverage. |
| [Sentinel-2](https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2), [terms](https://dataspace.copernicus.eu/terms-and-conditions) | Optical bands 10/20/60 m from 2015; reflectance is dimensionless after product scale/offset, cloud/shadow QA required. | Copernicus free/full/open policy; account often needed for downloads. Preseason features may use only prior available acquisitions; in-season NDVI is not a preseason predictor. |
| [Landsat surface reflectance](https://www.usgs.gov/landsat-missions/landsat-surface-reflectance), [scaling](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products) | 30 m optical history from 1982 across sensors. Collection 2 reflectance scale 0.0000275, offset -0.2; product QA mandatory. | USGS open data, account or public cloud access depending endpoint. Preserve sensor/collection/product processing dates; supports older ARDN seasons unlike Sentinel-2. |
| [NASA HLS](https://hls.gsfc.nasa.gov/about-hls/) | Harmonized Landsat/Sentinel reflectance at 30 m, recent years; typical 2–3-day processing delay. | NASA open data/Earthdata workflows. Do not count HLS and its source imagery as independent observations. |
| [MOD13 v6.1 guide](https://modis-land.gsfc.nasa.gov/pdf/MOD13_User_Guide_V61.pdf) | MOD13Q1 NDVI/EVI, 250 m, 16-day composites from 2000; scale 0.0001. Composite availability is after the window, not its start. | NASA open data, Earthdata authentication for many endpoints; use detailed QA. Mixed pixels and clouds limit field interpretation. |
| [MIRCA2000 v1.1](https://zenodo.org/records/7422506) | Irrigated/rainfed crop areas and calendars around 2000, coarse global grid. Historical calendar priors, not the field's actual sowing date. | Repository license CC BY 4.0. Pin file/version; modern calendars require newer evidence. Large archives not downloaded in this task. |
| [FAO GAEZ](https://www.fao.org/gaez/en) | Latest v5 released 2025, baseline 2020; v4 uses 2010 baseline. Suitability/potential yields depend on scenario, inputs and management. | Public portal; verify each selected asset's terms. Potential yield is not observed ground truth or a field recommendation guarantee. |
| [FAOSTAT crops](https://www.fao.org/faostat/en/#data/QCL), [database terms](https://www.fao.org/contact-us/terms/db-terms-of-use/en/) | Global annual national production/area/yield; read each row's unit and flags, conversion/moisture basis and release dates. | Public bulk/API, FAO CC BY 4.0 default subject to database/third-party terms. Strong broad sanity baseline; national aggregation cannot validate plot yield. |
| [Eurostat regional crops](https://ec.europa.eu/eurostat/databrowser/view/apro_cpshr/default/table), [metadata](https://ec.europa.eu/eurostat/cache/metadata/en/apro_cp_esms.htm) | European annual NUTS crop statistics; standard-EU versus national-moisture products differ; revisions/preliminary flags matter. | Public API, no paid access. Preserve Eurostat attribution/reuse conditions and geography versions. Do not treat missing/confidential values as zero. |
| [CYBench v1.10](https://zenodo.org/records/17279151), [benchmark paper](https://essd.copernicus.org/articles/18/3997/2026/) | Administrative maize/wheat yield benchmark: 149,393 maize and 65,862 wheat labels across 43 countries in the published study. Full v1.10 ZIP is 6,228,749,411 bytes, not acquired. | Zenodo metadata says EUPL-1.2; constituent source licenses also apply. Not a generic CC-BY field dataset. Useful next global administrative benchmark; not Ukraine/other-crop evidence by itself. |
| [USDA ARDN metadata](https://api.figshare.com/v2/articles/24665640), [parent CSCAP](https://api.figshare.com/v2/articles/24851877) | US plot trials 2011–2015; weather/soil/operations plus dry kg/ha yields; raw CSV cp1252. Joined dates and physical aliases matter. | Article license CC0. February 2026 notice warns nitrogen fertilizer assignments are wrong for several experiments; no nitrogen-response fitting. Preserve source snapshot and metadata, not only model weights. |

## Research branch inventory and next evidence

Ruslan's `468a03d` research snapshot contains 84 files totaling 6,277,997 bytes,
including Python, JSON reports, figures and model text, but **no raw CSV, Parquet,
ZIP or populated data cache** in `research/fieldshift_tasks`. Its `.gitignore`
excludes `data/` and `cache/`. Reported SMAP 65 points/42 usable and approximately
29,000 days are report claims, not locally available training rows. Model metadata
mentions 44,705 station-days/53 stations; county-yield reports mention 3,262 maize
county-years. Those inputs must be acquired and checksummed before reproduction.
The county model's May–August weather predictors are in-season and its leave-one-
year-out fitting can train on later years: it cannot substantiate preseason skill.

The next useful acquisition is a source-licensed CYBench administrative subset
with forward-year and country/region holdouts, plus new field trials in distinct
climates and additional crops. Keep plot, county and national evaluation separate.
Use NASA to supply missing forcing and independent seasonal soil-moisture proxies;
validate the incremental gain through a frozen ablation rather than assuming more
NASA products create independent evidence. SMAP assimilates meteorological models,
POWER/ARDN can share MERRA, and HLS contains Landsat/Sentinel inputs.

A conditional crop-engine experiment may still be useful with explicit generic
parameter ensembles, but missing initial root-zone water and validated management
prevent calling its yield error a calibrated field-physics accuracy result. This
empirical benchmark does not invent those inputs. New crop-choice recommendations
need counterfactual or randomized multi-crop evidence, decision costs, regional
calendars, and an abstention policy outside supported climate/soil/management space.
