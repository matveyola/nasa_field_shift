# Terrain directions

Field Shift can display **potential downhill surface directions** derived from the
public [Copernicus DEM GLO-30 COG dataset](https://registry.opendata.aws/copernicus-dem/).
The source is a digital **surface** model, approximately 30 m, including vegetation
and buildings. The AWS dataset is the 2021 release. Primary TanDEM-X acquisitions
were in 2011–2015; older elevation products may fill gaps. It is not a live feed.

These arrows are not observed runoff, a drainage network, a flood forecast, a
water-volume estimate or an irrigation recommendation. Agricultural drains, wheel
tracks, furrows and small earthworks are unresolved. Direction accuracy has not
been validated against measured water movement. Slopes below 1% are suppressed to
reduce flat-surface noise; this threshold does not make the remaining directions
hydrologically validated.

## Calculation and delivery

- `POST /api/fields/{field_id}/terrain/refresh` returns an existing or newly queued
  durable `geo_ml` job. Concurrent refreshes share an internal key based on field,
  boundary revision and method version. Pending work survives page reloads.
- A successful result is reused because the source is static. Terminal failures
  can be retried manually with a new job; old jobs and attempts remain preserved.
  A new field boundary or method version gets a separate job. No periodic terrain
  refresh downloads the same source repeatedly.
- `GET /api/fields/{field_id}/terrain` reads only completed PostgreSQL results for
  the current boundary revision and method version. It performs no network fetch.
- The worker reads only a bounded window from at most four public COG tiles. The
  field and a 90 m contextual border are projected to the local UTM zone on a 30 m
  grid, at most 256 × 256 pixels (fields up to about 7 km across).
- Elevation is bilinearly reprojected. Horn's 3 × 3 derivative uses projected metre
  spacing, requires finite elevations throughout a complete interior field
  neighborhood, and produces at most 200 arrows spaced at least 60 m apart.
  Arrows remain inside the field. Display length is fixed and is not water speed.
- Missing and out-of-range elevations remain missing. Less than 90% field coverage
  produces an explicit unavailable result. Flat terrain produces valid elevation
  statistics and no arrows. No public tile never becomes an assumed zero surface.

Per HTTP request the timeout is 8 seconds (connect 5 seconds, no internal retries).
The fetch loop checks a 75-second deadline between tiles; the existing worker hard
limit of 120 seconds is the final execution bound. GDAL cache is limited to 16 MiB,
HTTP cache to 4 MiB and single-thread warp memory to 8 MiB. Raw source windows are
bounded to 1024 × 1024 pixels. PostgreSQL remains the job and result authority;
the normal fenced attempts, retry limit, outbox replay and expired-lease recovery
apply. Transient network errors retry; unsupported footprint or missing public
coverage is saved as an explicit unavailable result.

The response carries source URLs, release, native resolution, method version,
coverage, limitations and the required modified-data attribution. See the
[Copernicus product description and licence notices](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM)
and the [AWS COG layout documentation](https://copernicus-dem-90m.s3.amazonaws.com/readme.html).

## Verification evidence

Deterministic tests in `apps/api/tests/test_terrain.py` cover projected north/west
directions and slope units, southern-hemisphere UTM and tile naming, flat and
missing surfaces, coverage limits, footprint/resource bounds, bounded reads,
boundary/method invalidation and durable job delivery.

The live probe in `.omc/artifacts/terrain-live-probe.py` writes
`.omc/artifacts/terrain-live-evidence.json` (ignored operational artifacts). On
2026-11-09 UTC it retrieved actual GLO-30 data for all three Iowa demo geometries
and two independently outlined Ukrainian GloCAB holdout fields. Coverage was 100%
on all five. Iowa arrow counts were 16, 8 and 60; Ukrainian `glocab-20140` and
`glocab-19975` yielded 14 and 47. Their elevation ranges were 36.09–42.19 m and
32.96–39.39 m. Individual fetch/processing runs took 0.047–0.812 seconds on the
development machine, including reuse of GDAL's bounded tile cache. These checks
establish real data delivery and bounded computation, **not measured runoff
accuracy**. The Ukrainian reference outlines originate from
[GloCAB](https://doi.org/10.5281/zenodo.10479122).
