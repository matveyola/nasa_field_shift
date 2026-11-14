# Field profiles and spatial eligibility (lane A, spatial-v1)

This lane produces planning-v1 DTOs without changing the legacy planner or crop
enum. The profile router, models and optional provider dependencies need explicit
registration by #7. A successful eligibility check is neither a yield prediction
nor an agronomic release decision. No empirical accuracy was measured here.

## Persistence and transport

`planning_field_profiles` stores append-only JSON snapshots, including dated crop
history with confirmed/reported/unknown status, soil horizons, management, sources
and geometry/data revisions. PUT serializes on the baseline field row, compares
`expected_revision` (null is create-only), generates a new snapshot ID and profile
revision and commits atomically. Farm-scoped request IDs provide exact-payload
replay; changed payloads conflict. Unknown history is never filled from legacy
crop strings. Future source availability is rejected by the frozen DTO validator.
Snapshot updates/deletes are not exposed. Retention/removal requires a separate
administrative policy; this lane does not add a DB immutability trigger.

Latest GET rejects changed geometry revisions. Historical `get_snapshot` remains
readable within its farm for reproducibility; a historical read is not permission
to apply it. C must check all live input versions transactionally when applying.
Full polygon/hole topology, baseline boundary equivalence and spherical area
(with 2%/0.01 ha tolerance) are checked on writes. Coordinates are longitude then
latitude. A user-entered provenance assertion is not a provider verification or
permission to treat an arbitrary soil source as a land mask.

Router: `modules.fields.planning_profile.routes.router`, mounted under `/api`:

- GET `/planning-v1/farms/{farm_id}/fields/{field_id}/profile`
- PUT same path, `ProfileWriteRequest` → service-owned `FieldProfileSnapshot`

The router requires existing request-state `Principal`; viewers read, editors and
admins write. Cross-farm requests return 404. The shared access middleware must
remain installed for session authentication and CSRF. The isolated test app uses
an explicitly synthetic principal middleware only in tests. Domain errors use
`PlanningError` (403/404/409/422). Malformed JSON/body-shape failures are
normalized by the isolated route class into PlanningError. #7 must enforce
request-size limits at the shared application boundary. Strict wire
DTOs are parsed in JSON mode by an adapter BeforeValidator, preserving date/tuple semantics;
FastAPI's Python-dict validation cannot directly parse these strict contracts.

## Providers, coverage and cache

`providers.py` defines typed soil, terrain, land, GAEZ, weather and hazard ports.
Unavailable adapters explicitly return unsupported/insufficient_data/error with
null values and reasons. No provider credentials or production fake datasets are
bundled. SourceRecord retains product/version, checksum, licence/attribution,
acquisition/availability/retrieval, spatial method, native resolution and coverage.
Actual land masks/GAEZ/DEM and weather acquisition remain injected adapters; ports
do not assert a working live provider. SSURGO stays US-only and is not called by
the global SoilGrids adapter.

SoilGrids 2.0: use pinned native-grid GeoTIFF WebDAV tiles or WCS subsets, cached
locally using `RasterCache`. Operator-configured credential-free HTTPS URLs and
host allowlists are required. Expected SHA256 is mandatory, writes are atomic,
cache hits recheck contents; interrupted/corrupt downloads never become valid
soil. Default 64 MiB transfer, maximum 256 MiB; 20-second socket timeout, 30-second
stream deadline (an in-progress read may add one timeout). Acquisition executes
outside HTTP requests. No automatic retry: retry explicitly with the same pinned
asset; content addressing makes successful repeats idempotent. Upstream asset
manifests retain the original retrieval time; reading cache must not refresh it.

`SoilGridsAdapter` checks source availability, age (default 30 days), unit/depth,
asset hash and complete polygon pixel coverage. It bounds reads to 250,000 pixels
and local assets to 256 MiB. Equal-area native grids are required; pixel-center
edge selection and equal native pixel weights are an approximation, not surveyed
field measurements. Off-raster cells and NoData reduce coverage; incomplete
coverage abstains rather than treating a valid subset as the whole field. Small
polygons with no native pixel centers abstain; an independently documented
representative-point adapter may be supplied. VRT trees must be materialized as
pinned subsets with dependencies verified upstream; hashing a VRT alone does not
pin referenced tiles. Arbitrary remote VRTs are not downloaded by the cache.

Mapped sand/silt/clay g/kg divide by 1000 to become fractions. SOC dg/kg and pH×10
divide by 10. Only standard 0–5, 5–15, 15–30, 30–60, 60–100, 100–200 cm intervals
are accepted. Hydraulic capacities and live moisture are not inferred from texture.
SoilGrids horizons remain partial until required measurements/validated hydraulic
inputs are provided; the suitability engine abstains on missing required soil QC.
SoilGrids data are modelled estimates, not sensor observations. Use its specific
CC BY 4.0 attribution with each pinned asset. REST outage never maps to dry soil.
See [ISRIC layers](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html)
and [access/licence](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html).

`planning_weather_issues` preserves full frozen scenario sets and their canonical
hashes per farm/set ID, including issue/cycle/member/grid/resolution/valid dates.
Changed content under the same ID conflicts. Callers must authorize farm scope
and resolve trace snapshots before archival; low-level archive is not an HTTP
endpoint. Identical grid IDs/days remain identical across adjacent or remote
fields; this adapter does not create artificial geographic variation. It does not
claim a populated historical forecast archive or infer operational runs from
reanalysis. Archive writes join the caller transaction; caller handles unique-key
race rollback/retry. No worker tasks, queues or training are added here.

## Solar and hazard evidence

`solar_position` uses optional **pvlib 0.16.1**, NREL NumPy SPA, UTC, latitude /
longitude decimal degrees, elevation metres, zenith from vertical and azimuth
clockwise from true north. Elevation missing is an error, not sea level. Flux
inputs retain GHI/DNI/DHI in W/m²; absent daytime flux is null. Ground-level
night flux is zero. No clear-sky weather, canopy response or yield multiplier is
invented. Terrain flat aspect remains null under the frozen DTO. See
[pvlib position](https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.solarposition.get_solarposition.html).

The root API dependency lock intentionally remains unchanged, as owned by #7.
To exercise this optional adapter install `pvlib==0.16.1` in the test environment
using `uv pip install --python apps/api/.venv/bin/python pvlib==0.16.1` after the
normal locked API install. #7 must add/review pvlib and its transitive dependencies
in the shared manifest/lock before enabling solar in production. Missing pvlib
raises explicit unavailable error; there is no approximation fallback. Offline
solar tests require this extra and deliberately do not skip when it is absent.

`threshold_evidence` separately reports wind/frost/heat/daily-rain extrema against
explicit versioned thresholds; coverage gaps produce null. Values are extrema in
the original units, not probabilities. `inundation_impact` reports unsupported
without hydraulic field depth. EFI/river warnings stay typed evidence: neither is
a crop-loss probability or field inundation depth. Hydraulic depth alone still
requires a validated crop response model from B.

## Eligibility and sequence handoff

`SuitabilityEngine` implements frozen `SuitabilityProvider.assess/candidates`.
Inject `Rulebook` and an explicit region resolver. Each rule records exact crop /
cultivar/profile, family, region, source, review status, calendar/harvest year,
purpose, thermal threshold, continuous soil depth/pH, irrigation systems, necessary
seasonal rain+maximum irrigation supply, return intervals and model capabilities.
There is no default crop rulebook, continent rule or automatic cultivar fallback.
Unreviewed/synthetic rules abstain in production. `allow_synthetic=True` is used
only by controlled tests; the engine never promotes synthetic rules by default.

Thermal time is sum(max(0, mean daily temperature − base)) over the declared crop
window. Missing required days or variables abstain. Frozen-soil new-sowing checks
are distinct from overwintering capability requirements. Necessary seasonal water
supply is not a daily water balance or yield response: B owns stress, storage
carry and capability execution. Rich soil cannot bypass water/calendar rules.
Unknown/reported prior crops cannot satisfy mandatory confirmed-history checks.
Planned earlier assignments constrain later return intervals without becoming
observed historical facts. Non-arable/ice/ocean rejection requires complete valid
source coverage; coordinates themselves prove nothing. Integration must supply
verified land evidence rather than mixing untrusted source roles.

Candidates preserve commitment IDs and locked assignments, report calendar
conflicts without unlocking, and retain rejected/unknown diagnostics. C may
select only fully eligible sequences with B's required capabilities satisfied.
Enumeration is deterministic and capped by max_candidates_per_field (≤1000);
it is a prefix, not an exhaustive optimization guarantee. At most 30 fields ×
1000 candidates × 3 seasons; each rule examines at most 64 × 2192 weather days.
Large bounds are contract ceilings, not latency promises. #7 must impose measured
CPU/payload limits and dispatch expensive work through durable jobs. This lane
performs no training, provider calls from GET/PUT, model runs or optimization.

## Integration registration

```python
# Add through #7's owned registry/entrypoints, not from this lane.
from field_shift.modules.fields.planning_profile import models
from field_shift.modules.fields.planning_profile import weather_archive
from field_shift.modules.fields.planning_profile.routes import router
app.include_router(router, prefix="/api")
# Inject ProfileRepository(session) and SuitabilityEngine(reviewed_rulebook,
# region_for=verified_region_resolver) into owning authenticated services.
```

Migration `0011_field_profiles` descends from `0010_precision_plans`; FKs reference
only baseline farms/fields. #7 merges it with C's `0012_planning_runs` using
`0013_merge_planning`, then restores a single Alembic head. Do not autogenerate a
replacement migration or run `upgrade head` on the unmerged dual-head tree.
Database rollback drops the lane tables and loses their data; shared operational
rollback requires a tested backup restore to a fresh database, beyond code revert.

Required runtime configuration is injected typed provider manifests, raster cache
path, reviewed rules/region resolver and an isolated DB URL. No new secret names
are consumed in this lane. Future acquisition adapters must declare their actual
provider credential names when implemented; neither GAEZ nor a paid weather
product is assumed available with an unrelated key.

## Verification and remaining release limits

Run affected offline tests and Ruff from the repository root, then full API fast
suite from `apps/api` (its Alembic relative paths require that working directory):

```bash
apps/api/.venv/bin/python -m pytest -q -m 'not integration' apps/api/tests/planning_profile apps/api/tests/planning_suitability
(cd apps/api && .venv/bin/python -m pytest -q -m 'not integration')
# Disposable PostgreSQL only; test upgrades/downgrades and writes test data:
ISSUE3_DATABASE_URL=postgresql+psycopg://USER@HOST:PORT/DISPOSABLE_DB apps/api/.venv/bin/python -m pytest -q -m integration apps/api/tests/planning_profile
```

Controlled predicate tests cover A's inputs/eligibility portions of G01–G04,
G07–G08, G12–G17, G20 and G22. G03 uses frozen artificial 600/2000 degree-day
profiles; optional frozen-soil state is an explicitly added controlled assumption.
G01 declares a three-day test window, rather than inventing an implicit year.
G13 storage/stress ordering and model capability impacts remain B's responsibility;
A tests exact preservation of differing water/soil inputs, not model predictions.
Solar tests cover both hemispheres and solstices against independent declination
sanity bounds and pvlib SPA; no universal aspect/yield factor is introduced.
Numerical raster means use 1e-12 tolerance because floating arithmetic need not
represent 0.4 exactly. Geo fixtures establish invariants, not accuracy or validated
production agronomy. All frozen case/binding files remain unchanged.

No live data acquisition, local agronomist review, paid API/GPU calls or empirical
benchmark is performed. Actual provider feeds, shared dependency lock changes,
body limits, model capability wiring and final
migration merge are explicit #7 handoff requirements. These do not turn missing
sources into usable production measurements or constitute a deployment approval.

Feature-worktree verification: **423 API fast tests passed** (7 infrastructure
checks deselected), **43 affected offline tests passed**, PostgreSQL 18 isolated
migration/rollback/upgrade plus concurrent CAS **1 passed**, **73 web tests
passed**, web/contract TypeScript, frozen export freshness, Ajv/case checks and
Ruff/format checks passed. Optional pvlib 0.16.1 was installed explicitly for solar
tests. Docker build was attempted but could not connect to the daemon socket
(permission denied); no container build pass is claimed. Other infrastructure
suites, live providers and empirical model evaluations were not run. Combined-tree
checks and published SHAs are recorded in the issue completion report. Shared
`docs/status.md` remains reserved for #7, as required by the ownership pack.
