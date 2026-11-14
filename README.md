# Field Shift

[Product vision](docs/vision.md) · [Delivery roadmap and rollback](docs/roadmap.md) · [Verified status](docs/status.md)

Field Shift is a local agritech MVP. It combines a georeferenced field map, a historical and five-day root-zone water balance, a three-year rotation planner with Year 1 weather replay, a field-aware demo agent, and shared farm settings. Fields shows historical ERA5-Land, ECMWF surface moisture forecasts, dated Sentinel-2 imagery and optional sensor-trained estimates. Farm edits, plans and job state persist in PostgreSQL. See the [data sources and model assumptions](docs/data-sources.md).

Field analytics adds a cumulative water budget, seasonal Sentinel-2 NDVI, within-field vegetation variation, field priorities and evidence-linked assistant actions. Optional Copernicus DEM arrows show potential downhill directions. See [using field analytics](docs/field-analytics.md), [satellite processing](docs/vegetation-analysis.md) and [terrain assumptions](docs/terrain-directions.md).

The Assistant can connect to the private agent service in `services/agent` through a Compose profile. See [assistant setup and supported evidence](docs/assistant-integration.md).

## Start

Install Docker with Compose, then run from the repository root.

```sh
docker compose up --build -d
docker compose ps
```

Open [Field Shift](http://localhost:8080). Compose migrates and seeds the database once per startup and starts the HTTP API, web UI, dispatcher, dedicated simulation and geospatial workers, daily soil moisture refresh, Redis, and the backup process. Set `WEB_PORT` in a copy of `.env.example` to change the browser port. The demo is bound to localhost and uses a single shared farm. The default local mode uses a shared farm. For team accounts, permissions and deployment constraints, see [team access](docs/team-access.md). Tenant isolation still requires separate production validation.

For UI work, run `npm ci` and `npm run dev` in `apps/web`. Its development proxy expects an API on `127.0.0.1:8000` by default. In `apps/api`, run `uv sync --locked --extra dev`, `uv run alembic upgrade head`, `uv run python -m field_shift.seed`, then `uv run uvicorn field_shift.api:app`. The API defaults to local SQLite for lightweight UI work. Use Compose and PostgreSQL when running jobs and testing recovery.

On bare Debian/Ubuntu, install `libexpat1` for Rasterio's native runtime. The Docker image includes it and checks Rasterio/Shapely imports during its build.

In **Fields**, use **Edit boundary** to move, insert, or remove vertices when a field expands or changes shape. The server recalculates the mapped field area when the outline changes. Use **Edit planted area** to mark one partial footprint for the active season, or select **Entire field** to clear the partial footprint. This keeps the physical boundary and farm area independent of the season’s planted area. The demo outlines were traced manually from NAIP imagery. When a boundary changes, older analyses remain in job history and the next refresh uses the new geometry.

## Code boundaries

- `apps/web/src/app` owns the shell, routes and shared field/season/scenario. Feature directories own page behavior. `apps/web/src/api` owns the HTTP client and contracts.
- `apps/api/src/field_shift/modules` contains the Python domain modules and their API endpoints. Replace the mock agent and model providers in the owning modules while preserving their input and output contracts.
- `apps/api/src/field_shift/modules/agent` owns chat, field context and evidence. `services/agent` runs the private model inference service with its own Python dependencies.
- `apps/api/src/field_shift/jobs` owns submission, dispatch, execution, persistence and recovery. PostgreSQL migrations live in `apps/api/migrations`.
- `packages/field-shift-ml` owns offline data acquisition, training, evaluation and frozen reports. Its locked Python environment is separate from the API and worker images. Runtime model loading and validation stay with their API domains.
- `infra/backup.sh` writes PostgreSQL archives. Compose runs it independently of the API, workers and Redis.

## Job guarantees

The API commits a job before returning its ID. The dispatcher scans PostgreSQL and sends the job ID to `simulation` or `geo_ml`. Workers claim work with a row lock, then run the provider outside the transaction. Only the current, unexpired attempt can commit a result. Celery uses late acknowledgement. The dispatcher resends unclaimed work after a bounded backoff and recovers expired worker leases. Three failed or abandoned attempts put the job in a durable terminal failed state.

Delivery can occur more than once. A provider should calculate from its inputs and return a result. If a future provider writes artifacts, use attempt-specific keys and publish the final reference through the fenced database transaction. Supply the same `Idempotency-Key` when repeating an HTTP submission. Reusing the key with a different payload returns a conflict.

The Compose worker uses a 120-second hard limit and a 135-second lease by default. When integrating a longer model, set `JOB_TASK_TIME_LIMIT_SECONDS` and `JOB_LEASE_GRACE_SECONDS` consistently for the dispatcher and both workers, and keep the worker shutdown grace period appropriate for your deployment. The dispatcher can resume from PostgreSQL after a Redis restart or loss of queued messages.

## Soil moisture operations

The `moisture-refresh` service checks on startup and every hour by default. ERA5-Land publishes hourly values in daily releases about five days behind real time. For the historical moisture layer, the worker checks the expected release day and its predecessor, and uses the newest day with 24 valid UTC hours. If the first job falls back to the previous day, the scheduler checks once more after twelve hours if the target date is still current. The historical ML model keeps its six-day input cutoff. For the forecast, the scheduler requests the previous day's 00:00 UTC ECMWF IFS initialization for leads 1–5.

The schedule deduplicates by field, geometry, data date and provider version. The worker commits results through the normal PostgreSQL job transaction. After a transient source failure or expired worker lease exhausts the job's attempts, the scheduler can submit a new job after 2, 4, 8 and 8 hours from each preceding failure, while that date is still the current scheduled target. Each attempt remains in PostgreSQL. Fields preserves the most recent successful archive day and indicates an older forecast. Set `MOISTURE_FORECAST_REFRESH=0` to turn off automatic forecast refresh.

ERA5-Land is the default archive layer. On the new farm holdout the v2 correction improved RMSE over v1, and ERA5-Land remained ahead on MAE and within the v2 serving footprint. For a controlled preview, set `MOISTURE_ENABLE_EXPERIMENTAL=1` and `MOISTURE_MODEL_PREVIEW=v2` in the worker, API and scheduler. The preview refreshes for supported locations and months and shows its soil survey, version, prediction interval and observed coverage. The forecast correction has a separate gate based on the same IFS cycle and forecast lead. The active forecast uses native IFS whenever that gate fails. See the [model card](packages/field-shift-ml/README.md) and [forecast protocol](packages/field-shift-ml/forecast-protocol.md).

Set `MOISTURE_REFRESH_INTERVAL_SECONDS` to change how often the scheduler checks. Use `docker compose exec moisture-refresh python -m field_shift.modules.geo_ml.refresh --once` to check immediately and `docker compose logs moisture-refresh worker-geo-ml dispatcher` to inspect scheduling and delivery. A dated manual request in Fields goes through the same worker. To roll back a model, deploy a previous API image for the worker and scheduler together. Existing results retain their model version.

Commercial deployments using the Open-Meteo archive need a commercial account and a server-side `OPEN_METEO_API_KEY` with archive access. Configure this through the deployment's secrets. The local stack uses the free endpoint for development. See the [provider terms](https://open-meteo.com/en/terms).

## Water and Rotations

Water's five-day forecast uses one ECMWF IFS 00 UTC run. The initialization day's modeled soil layers supply the opening reserve. The next five full UTC days supply liquid rain and reference ET₀. The same FAO-56 equations drive the historical balance and the forecast. The forecast keeps the physical mass balance for every day and can publish a separate learned reserve only after its release gate passes. Published predictions show the initialization date and modeled depth. The bucket uses reference loam, a fixed crop coefficient and zero irrigation. [Root-zone validation](packages/field-shift-ml/root-zone-protocol.md)

The refresh process queues Water forecasts through the `simulation` worker and deduplicates by field, crop, model and initialization date. Failed upstream calls use the same durable retry and scheduled replay policy. A successful previous run remains available when a later run fails. Set `WATER_FORECAST_REFRESH=0` to disable scheduled Water jobs. The manual refresh button remains available.

Water and the surface moisture forecast share validated IFS inputs for the same requested coordinates and initialization date. Redis holds up to 256 entries of 16 KiB each with a 48-hour expiry. The workers fetch directly if the cache is unavailable. Set `IFS_FORECAST_CACHE=0` to disable sharing.

Rotations keeps its water comparison and adds a separate estimate of maize yield change relative to continuous maize. The estimate applies within the measured predecessor, management and geographic scope. The plan's first predecessor comes from the field crop when its recorded season matches the plan. A rejected candidate can be examined in a controlled deployment with `ROTATION_YIELD_ENABLE_EXPERIMENTAL=1`. [Yield evaluation and data](packages/field-shift-ml/rotation-yield-protocol.md)

## Backups

Compose creates a custom-format PostgreSQL archive at startup and hourly by default, checks that PostgreSQL can read the archive catalogue, and retains seven days of archives under `backups/`. Change the interval, retention, and destination through `.env`. The directory is on the host and may be mapped to a dedicated backup location. Redis AOF reduces broker downtime and data loss. PostgreSQL and its archives contain the authoritative job records.

Restore a selected archive to a fresh database, verify its contents, then point the application to that database. Use the same PostgreSQL major version as the backup container. Inspect the archive with `pg_restore --list <archive>`, then restore with `pg_restore --exit-on-error --no-owner --dbname=<fresh_database> <archive>`. For host-loss protection, store copies on a separate disk or backup service.

## Verification

During development, use Ruff and tests that cover the changed code. The repository instructions describe the full policy.

```sh
cd apps/api
uv run ruff check src/field_shift/jobs tests
uv run pytest -q -m "not integration"
```

```sh
cd apps/web
npm test
npm run build
```

Run PostgreSQL and Redis integration checks with `uv run pytest -q -m integration` from `apps/api`. Set `FIELD_SHIFT_TEST_DATABASE_URL` to a PostgreSQL database where tests can create and drop their own temporary schemas. Set `FIELD_SHIFT_TEST_REDIS_URL` to a dedicated empty Redis database. CI provisions both. The release workflow builds downloadable Docker image archives on version tags or on manual dispatch. Load both archives with `docker load`, set `API_IMAGE` and `WEB_IMAGE` to the corresponding SHA-tagged images, and start Compose with `--no-build`.
