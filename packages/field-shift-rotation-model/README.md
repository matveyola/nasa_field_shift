# Experimental rotation model bridge

The installed `field_shift_rotation_model` namespace executes the team's
`research/rotation-handoff` at a68b67c70a470bf8a5ba8f9f8e59350f029200fc. The original
research directory remains byte-identical. Packaged `upstream-receipt.json` records
original file hashes, and `NOTICE.md` preserves the code/data rights limitations.
The port changes namespace, resource loading and execution boundaries, not the
scientific formulas. It does not promote any of the existing model release gates.

## Capabilities and interpretation

The catalog exposes all 13 original crop profiles, seven northern spring/summer
climate crops, 13 equipment options and 20 cultivar references. Forecast mode
compares two or three plans over one to three supplied crop cycles; it combines
current public weather with historical analogues and is not a calibrated multiyear
forecast. Climate mode is explicitly conditional: 2040–2049, March–July sowing,
northern latitude 0–70, at most three decisions spanning five calendar years.
These are engine applicability limits, not geographical suitability claims.
Rapeseed has no sourced default calendar and remains unavailable in the public
forecast request. The allocation catalog and validated yield capabilities remain
separate. No broad pulse or wheat-cycle substitution is made.

Original result payloads and ranks remain intact. A separate interpretation
envelope prevents a recommendation when trusted current arable-land evidence is
missing, a whole forecast cycle is cold/zero-demand, its reference flowering heat
is unmet, or a climate cycle ends without maturity. An arable gate alone does not
validate crop suitability. Soil, source calendars and field-scale assumptions
remain disclosed. Climate opening water at 50%, drainage at 10%, and Sutherland's
50/50 blend are inherited scenario assumptions, not measured states or calibrated
probabilities. Reference yield times price is gross revenue, not profit.

## Installed private boundary

`uv sync --locked` installs this package. The optional `--extra benchmark` adds
`field-shift-science` only for the paired benchmark. Production requires no
checkout paths or import-path injection. `field-shift-rotation-runtime` serves an
authenticated private HTTP interface; `services/rotation-model/Dockerfile` builds
a noneditable nonroot image. Set `FIELD_SHIFT_ROTATION_MODEL_TOKEN` to at least
32 characters. API/worker configuration additionally requires
`FIELD_SHIFT_ROTATION_MODEL_URL`. The main API never imports this distribution.

Each request executes in a fresh spawned child, one concurrent child per service,
with 50 seconds wall/CPU, 2 GiB virtual memory, and 24 MiB request/response bounds.
Each HTTP phase has a 55-second worker timeout; the existing worker task bound
must be at least 115 seconds (default 120). Busy/unavailable phases fail explicitly.
No model or weather is fetched on import or catalog access.

The worker binds the actual owned field coordinates and boundary at submission.
Acquisition requires explicit public-data consent and the current UTC decision
date. Anonymous fixed provider hosts only; no redirects, credentials or arbitrary
URLs. Responses are limited to 4 MiB each, 16 MiB total and 128 requests. Captured
bytes, checksums, URLs and actual availability times form immutable evidence.
Evaluation replays only those bytes and verifies engine, input and output hashes;
it never replaces a failed acquisition with fabricated data or backdates a vintage.
Packaged reference replay is available without network consent.

PostgreSQL owns queued/running/retrying/succeeded/failed state. The existing
simulation queue carries job IDs, attempts and late acknowledgements. Acquisition
is checkpointed in job result under its current token and live lease; retry reuses
that evidence. Existing maximum three attempts, lease grace and expired-job
recovery remain. The idempotency key binds farm, field and request ID; edited input
with the same ID conflicts. Explicit rerun after terminal failure needs a new ID.
Final success locks Field then Job in one transaction, rechecks owner, geometry and
current land gate, and invokes the existing attempt fence. Old attempts and changed
boundaries cannot commit results. Saved lists and seasonal metrics are farm-scoped
and current-boundary-only. Source percentages are copied, never reconstructed;
missing metrics stay null and overlapping stress classes are never added.

## Reproducible comparison and limits

Run `field-shift-rotation-benchmark --output /absolute/new/report.json` after
installing the benchmark extra. It refuses existing output paths. Protocol v1
compares the same public inputs, sourced reference phase lengths and explicit
opening/soil assumptions against existing `field_shift_science.layered_water`.
It includes 54 historical cases at three Iowa fields plus 72 conditional NASA
cases at two Iowa cells, six scenario members, four crops and one/two/three crop
years. Twelve field/crop historical intersections lack a calendar fitting the
complete archive season and are explicitly omitted. The Sutherland golden JSON
must match exactly. Daily storage, ET, demand, runoff, drainage, snow and stress
agreement and soil-plus-snow mass balance are asserted.

This is an equation replay comparison, not independent accuracy evidence. There
are no compatible observed field ET, water shortfall or rotation-profit targets
in the bundle; empirical MAE/RMSE/R²/bias/coverage/regret remain null and blocked.
Iowa replay does not establish global, paddy, tropical, Antarctic, high-altitude,
winter or locally calibrated suitability. No blending or winner promotion is
justified by agreement. Conditional future climate inputs are never described as
observed outcomes or forecasts issued at their target dates.

The author comparison of the port found 317 identical function ASTs after ignoring
imports/docstrings and normalizing the namespace. Deliberate differences are
installed engine/resource resolution, provider evidence capture timestamp,
removing cwd field discovery, external-only CLI outputs, equivalent `cache`
replacing unbounded `lru_cache`, and equivalent named sort keys replacing lambdas.
All original source files and reference artifacts remain checksum-receipted;
these mechanical checks supplement the numerical tests and do not prove accuracy.
