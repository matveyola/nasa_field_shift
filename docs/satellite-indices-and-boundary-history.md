# Satellite indices, boundary history and prepared contours

The spectral implementation extends the existing durable monthly Sentinel-2 pipeline.
Seven supported indices have numeric readouts, scene-specific overlays and seasonal
curves. NDVI and MSAVI use the existing B04/B08 windows. Green B03, red edge B05 and
SWIR B11 add only three bounded raster reads after a scene passes the cloud check.
All use harmonized L2A reflectance on the existing 20 m analysis grid. Unsupported,
masked and missing values stay missing. Each additional index carries its own
coverage and pixel count. A failed band download retries the durable job.

| Index | Formula using Sentinel-2 bands | Interpretation |
| --- | --- | --- |
| NDVI | (B08 − B04) / (B08 + B04) | Vegetation contrast |
| MSAVI2 | (2B08 + 1 − sqrt((2B08 + 1)² − 8(B08 − B04))) / 2 | Soil-adjusted vegetation contrast |
| NDRE | (B08 − B05) / (B08 + B05) | Red-edge vegetation contrast, explicitly the broad-NIR variant |
| RECI | B08 / B05 − 1 | Chlorophyll proxy, not calibrated chlorophyll concentration |
| MCARI | ((B05 − B04) − 0.2(B05 − B03)) × B05 / B04 | Chlorophyll absorption proxy |
| NDMI | (B08 − B11) / (B08 + B11) | Canopy moisture contrast, not volumetric soil moisture |
| NDWI | (B03 − B08) / (B03 + B08) | McFeeters open-water contrast |

Formula sources are [Sentinel Hub](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel/sentinel-2/)
and [USGS MSAVI](https://www.usgs.gov/landsat-missions/landsat-modified-soil-adjusted-vegetation-index).
PRI cannot faithfully be calculated without the required narrow 531/570 nm bands;
Sentinel-2 does not provide them. A calibrated SMI has no validated implementation
here and is not mislabeled NDMI. These are explicit differences from the competitor's
index menu, not unimplemented buttons.

Old NDVI snapshots remain readable. An explicit **Update satellite analysis** upgrades
saved months missing `sentinel-spectral-b08-v1`; background maintenance only handles
its existing subscribed/current-month budget. Acquisition identity and field-footprint
fencing still apply. Existing `geo_ml` retry, lease fencing, recovery and acknowledgement
policies apply. At most three candidate scenes and 65,536 grid pixels are processed;
the runner checks a 75 s deadline between candidates. Each HTTP raster read has an 8 s
timeout, and only a clear scene reads additional bands. Each map stays at at most about
2,500 display cells. Closed-month results avoid repeated data downloads after upgrade.

## Boundary events

Migration `0007_field_boundary_history` preserves the current boundary as a baseline,
then stores immutable boundary events with revision, effective season, geometry, area,
creation time and source. `PATCH /api/fields/{id}/boundary` accepts an optional
`effective_season`; absence means the active season. Saving an earlier season does not
overwrite a later season's geometry. Editing or restoring increments the global
boundary revision and invalidates jobs from earlier revisions. Concurrent stale writes
return 409. A restored contour is reviewed in the existing vertex editor and saved as
a new event; history is never rewritten. All saved planted areas must still fit the
resolved boundary for their seasons.

`GET /api/fields/{id}/boundary-history` lists events; `GET .../boundary-history/{season}`
resolves seasonal geometry. Seasons before the first recorded event use the earliest
known contour and return `earliest_boundary_reused: true`. That fallback is explicitly
described in the UI. Accurate prior-year contours can be added with the seasonal editor.
This is not evidence that historical boundaries were constant before records began.

## Open prepared Ukraine contours

The source is [NASA Harvest Ukraine field boundaries 2023](https://data.harvestportal.org/dataset/ukraine-field-boundary-delineation-2023),
licensed **CC BY**, created using [Sadeh et al. (2025)](https://doi.org/10.1038/s41597-025-05190-7).
They are historical remote-sensing estimates, not cadastral records or verified current
farm ownership. Private user fields are never exposed through the public contour table.

The explicit administrator importer streams a local ZIP Shapefile and uses fixed in-memory
components rather than extracting archive paths. It bounds archive size, metadata memory,
record count, imported window and output count. Re-running the same archive is idempotent
using its SHA-256 and source record number. Geometry is simplified in local UTM only when
needed, with at most 20 m tolerance and no more than 2% area change. Invalid polygons,
polygons with holes and geometries that exceed the app's 127-vertex bound are skipped.

```powershell
python -m field_shift.modules.fields.import_contours archive.zip `
  --region Zakarpattia --year 2023 --bbox 22.25 48.4 22.45 48.6 --limit 2000
```

The first verified import covered that bounded window, not the entire oblast or Ukraine.
The `prepared_contours` table has an indexed bounding box query. API windows are limited
to 0.2 degrees per side and 200 returned contours; truncation is explicit. The coverage
endpoint lists only actually imported records and their extent. Users can navigate to
coverage, choose a polygon on the map, edit it and explicitly save a new field.

Verification on the source archive found 1,263 intersecting records, imported 1,255 and
skipped 8 unsupported geometries. Source archive SHA-256 is
`225cd112cf91ca94cc71d7ace0f6fdbdc2c31c09f6c13e5616d3c8005823b701`.
Runtime import evidence and the compact public-data deployment snapshot live under
ignored `.omc/artifacts/`; no credentials or private field records are part of them.
