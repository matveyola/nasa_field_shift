# Sentinel-2 vegetation analysis

The `imagery` module calculates NDVI from real public Sentinel-2 L2A reflectance. It does
not infer a crop, disease, irrigation requirement or yield from NDVI. Farm names, crop
labels and seeded field outlines remain demo inputs; satellite observations are real.

## API and lifecycle

- `GET /api/fields/{id}/vegetation?season=2026` reads persisted monthly snapshots only.
  `include_cells=false` returns the same observations and zone statistics without map
  cells. SQL projects small metadata; only the newest displayed grid is loaded when needed.
- `GET /api/fields/{id}/vegetation/scene?season=2026&observation_date=2026-07-14&scene_id=...`
  returns one stored map with its matching observation. The request uses the acquisition
  date and scene ID and follows the same footprint and boundary revision checks. A missing
  matching grid returns `null`. The field chart selects this map, and the A/B control
  switches between two observations on the same map with a fixed NDVI scale.
- `POST /api/fields/{id}/vegetation/refresh?season=2026` durably queues at most twelve
  monthly `geo_ml` jobs and reuses existing active work. The submission list starts with
  the newest month; the shared dispatcher/broker does not guarantee execution priority.
  It does not run raster processing in an HTTP request. It can explicitly retry terminal
  failed jobs.
- The existing refresh loop rechecks requested current seasons after a 24-hour cooldown;
  its polling interval and worker queue determine when the next result is available.
  A partial month is completed at rollover, followed by one final catalog check after a
  seven-day publication grace period; completed monthly snapshots are then reused.
  The previous year's subscribed December is also finalized after New Year, including
  recovery after a service outage. Other archived months are not automatically rescanned,
  and a new calendar season requires an initial request before automatic updates begin.
  At most 24 new vegetation jobs are submitted per scheduler invocation; importing a field
  alone does not trigger a full-season download.
  Set `VEGETATION_REFRESH=0` on that process to disable scheduled vegetation refresh.
- A footprint SHA-256 includes the selected season's planted area, falling back to the
  field outline. Both this fingerprint and the existing boundary revision fence results.
  Editing a planting invalidates the old analysis even without a boundary revision change.

Each month selects one representative observation from up to three covering, verified
scenes, ordered by scene cloud cover then recency. This is a sampled seasonal series,
not daily observations or a promise to examine every scene. The response includes dates,
scene identifiers, source links, valid coverage and explicit per-month missing, cloud,
unsupported, pending and failure states. No missing observations are replaced with zero.

## Method and bounds

- Red (B04) and NIR (B08) 10 m reflectance are averaged onto a 20 m UTM analysis grid.
  NDVI is `(NIR - red) / (NIR + red)`. A 20 m SCL mask excludes cloud, shadow, snow,
  defective and unclassified pixels, with a one-pixel exclusion collar. Clear vegetation,
  bare soil and water remain eligible observations.
- New snapshots also calculate MSAVI from the same scaled reflectance and valid pixels
  using the [USGS MSAVI formula](https://www.usgs.gov/landsat-missions/landsat-modified-soil-adjusted-vegetation-index).
  The map uses the same stored cell geometry. Older snapshots remain readable, with MSAVI
  absent until a new snapshot is processed for the period. The seasonal CSV exports both
  date and source and leaves missing MSAVI values blank.
- Earth Search sometimes retains the original `-0.1` asset metadata after harmonizing
  the actual COG pixels. The item-level `earthsearch:boa_offset_applied=true` prevents a
  second subtraction. Modern scenes without explicit harmonization confirmation are
  excluded, not guessed. Pre-baseline-04 scenes need no BOA offset adjustment.
- At least ten valid pixel centres and 70% valid footprint coverage are required.
  Seasonal chart ranges are spatial 10th–90th percentiles, not prediction intervals.
- Low/high zones are below/above the observed field median by 0.15 NDVI. Low-area share
  uses valid observed pixel centres as its denominator, not the whole field area.
- Map cells are clipped to the actual footprint. Large display grids are aggregated;
  their resolution is explicit. Partly masked coarse blocks are omitted, so cloud gaps
  are never painted as observed vegetation. Statistics retain the native analysis grid.
- Maximum analysis window: 65,536 pixels, with dimensions at most 1,024 pixels.
  Larger or tiny unsupported fields return an explicit reason.
- Catalog responses are capped at 4 MiB and 100 scenes. Only allowlisted HTTPS COG hosts
  and expected B04/B08/SCL asset paths reach GDAL. HTTP timeouts and GDAL caches are bounded.
  Closing a raster evicts its HTTP byte ranges so a retry can fetch fresh bytes after a
  truncated response. Completed snapshots remain in PostgreSQL.
  The worker checks a 75-second processing budget between candidates. The existing
  120-second Celery hard limit is the final execution bound, including slow remote I/O.

PostgreSQL owns submissions and results. Each task uses the existing three-attempt retry,
lease fencing, acknowledgement-after-processing and dispatcher recovery path. An upstream
timeout is retryable; it is never persisted as "no clear scene". Killing a worker does not
remove the durable job. Redis loss does not erase completed monthly results. Existing
database backups cover these snapshots; no new independent storage service is required.

## Verification with public data

Live window reads were exercised in November 2026 (no private credentials):

| Footprint | Scene | Mean NDVI | Valid 20 m pixels | Valid coverage |
| --- | --- | ---: | ---: | ---: |
| Seeded US F-01 outline | S2B_15TVG_20260714_0_L2A | 0.8965 | 285 | 100% |
| Ukraine GloCAB `glocab-20140` reference | S2A_36TXS_20260728_0_L2A | 0.1530 | 300 | 100% |
| Ukraine GloCAB `glocab-19975` reference | S2A_36TXS_20260728_0_L2A | 0.7106 | 622 | 100% |

The Ukrainian outlines come from the existing held-out segmentation fixtures based on
[GloCAB](https://doi.org/10.5281/zenodo.10479122). These checks validate source delivery,
raster processing and polygon masking; they do not validate crop labels or establish
ground-truth NDVI accuracy. Reference boundaries can change between years.

Automated regression coverage includes radiometry, hostile asset URLs, SCL and missing
data, polygon clipping, coarse-grid gaps, resource bounds, contract validation, durable
worker results/retries, idempotency, current-season refresh, planting invalidation and
separation from historical soil-moisture results.

## Source documentation

- [Earth Search Sentinel-2 L2A](https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a)
- [Earth Search offset metadata issue](https://github.com/Element84/earth-search/issues/71)
- [Sentinel-2 scene classification](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/scene-classification/)
- [Rasterio windowed reading](https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html)
- [Rasterio reprojection](https://rasterio.readthedocs.io/en/stable/topics/reproject.html)

Map/UI attribution: "Contains modified Copernicus Sentinel data" with the acquisition year.
