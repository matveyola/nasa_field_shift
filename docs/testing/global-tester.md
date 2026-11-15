# Dedicated global application tester

This is an explicit operator tool for a **separate test deployment and database**.
The current application serves one farm (`FARM_ID=1`) per deployment. Creating a
second login in an existing deployment does not isolate its data. This tool does
not change that architecture or add an application startup hook.

## Create the isolated workspace

Use a new database, Redis namespace/project, service ports and private service
configuration. Apply the repository migrations **without `seed_demo`**, before
starting the API. Never point this command at the original farm database. The
operator must choose the separate database explicitly; the command does not fall
back to `DATABASE_URL` or the normal development database.

With the API distribution installed (editable development or a production wheel):

```bash
mkdir -m 700 /absolute/private-global-tester
export FIELD_SHIFT_TESTER_DATABASE_URL='postgresql+psycopg://.../dedicated_tester'
python -m field_shift.modules.testing \
  --credentials /absolute/private-global-tester/credentials.json \
  --season 2027 --confirm-isolated-database
```

Keep the real DSN out of shell history and logs. Supply it using the deployment's
normal private environment mechanism. The command refuses credential paths inside
Git checkouts, relative paths, symlinks/hardlinks, non-owned files and permissions
other than 0600; its directory must be owned and 0700. The password is generated,
never printed, and stored only in this private file. Configure the separate API
with team access and its own public origin; log in using the email/password from
the file. The seeder cannot enforce the settings of an independently started API.
Production still requires the normal HTTPS/secure-cookie configuration.

The receipt contains the random seed identity, database-identity digest (not the
DSN), fixture version/hash, generated credentials, G01–G24 to field-ID mapping and
full scenario/prompt plan. Do not publish this receipt or attach it to bug reports.
Actual execution results belong in separate private evidence files; do not edit
the immutable seed plan to make results appear to have been planned.

## Ownership and recovery

The first seed refuses any existing application data; migration version metadata
is allowed. Farm/fields are created through the existing domain models and field
service; account creation uses the ordinary auth bootstrap service. No password
reset, invitation adoption, schema creation, data deletion or original-farm copy
occurs. An outer transaction contains the domain service commits, including field
boundary history. A collision with another seed cannot replace its farm row.

An exclusive file lock serializes use of one receipt. The file is flushed before
DB writes so retry after a crash retains the same account secret. If writing the
file is interrupted, invalid JSON is refused; inspect the new isolated database
before replacing the receipt. A failed database operation rolls back the whole
seed. An empty, migrated DB can then retry using the same receipt.

A retry after commit checks the receipt version/hash, database, seed-specific farm
name, sole account/membership and password. It returns `already_seeded` without
rewriting fields or evidence. Subsequent QA edits are preserved. A changed farm
identity, membership, password or missing case is refused, never repaired by
resetting data. If the private receipt is lost, do not adopt an existing account:
use the normal operator recovery policy or create another isolated test workspace.
The dataset describes **initial** field inputs; after edits, fetch the current
field/boundary revision for every execution rather than treating the original
receipt as current field truth.

## Case scope and evidence

Twenty-four small rectangles are synthetic, not surveyed farms or evidence of
crop cultivation. Every field name and the farm name visibly say `SYNTHETIC`.
The normal field API may call these `registered`, meaning entered via the domain
service, not verified. The receipt preserves their synthetic provenance.
Crop strings are hypothetical user choices; no crop history, soil observation,
verified land source, planning profile or assistant message is fabricated.
The farm's `irrigation_available=False` is an explicit synthetic no-irrigation
baseline required by the existing domain schema, not an observed regional fact.
Budget/equipment remain missing. G10/G12 management variants are questions to
exercise later with explicit inputs, not seeded water rights or paddy capability.

| Cases | Location/scenario | Invariant under examination |
| --- | --- | --- |
| G01–G02 | Iowa and neighboring rectangle | Shared weather cannot imply identical soil/yield |
| G03–G04 | Kyiv and neighbor | Calendar reference and field identity remain distinct |
| G05–G06 | Rio urban/regional points | Coordinates do not establish arable land |
| G07–G08 | Antarctica/northern Finland | Missing land/thermal evidence, no zero-demand winner |
| G09–G10 | Same arid Egypt rectangle | Irrigation hypothesis does not grant water resources |
| G11–G12 | Same Bangladesh rectangle | Rice/paddy hypothesis needs supported management |
| G13–G14 | Andes/North Sea coast | Elevation/coastal labels are not measured inputs |
| G15–G16 | Southern Australia/cross-year cycle | Sowing and harvest years must not be conflated |
| G17–G18 | Equator/Japan rice | Multiple cycles and crop capabilities stay explicit |
| G19–G20 | Canadian prairie/India heat | Cultivar, maturity and heat need actual sources |
| G21–G22 | Chile/Cape | Missing water, prices or currencies cannot become profit |
| G23–G24 | Unknown Kyiv crop/Iowa peas | Unsupported species and missing soil/economics abstain |

Coordinates and exact plan content live in the installed Python `cases` module.
This is a test plan, not evidence that all these behaviors passed. No production
algorithm branches on case ID, test account, place name or country.

The seeder makes **no network calls**. Run the actual authenticated
`/api/fields/{field_id}/weather-summary` and rainfall-history/planting-opportunity
routes separately. Preserve returned scope, units, dates, source/version,
retrieval/availability metadata and raw-response SHA256 in external receipts.
If the provider does not expose a vintage/availability value, mark it unavailable;
do not invent it. Historical reconstruction is not a forecast issued in the past.
Provider unavailability and missing soil/economics are expected possible outcomes,
not permission to fill synthetic measurements into a real-data display.

Run the receipt's user prompts through the configured agent with the actual
selected field and boundary revision. Save actual responses, source citations,
tool requests/results and errors from that execution. A planned prompt is not a
chat history. No successful assistant response, tool call, agronomic winner,
profitability percentage or accuracy metric is precomputed by this package.

## Verification

`apps/api/tests/test_global_tester.py` exercises actual SQLite persistence,
ordinary password/bootstrap/login middleware, rollback/retry, existing-account
refusal, separate database isolation and private file handling. The suite does
not mock provider weather or assistant responses. Existing team-access and field
history tests cover affected domain boundaries. Production PostgreSQL execution,
actual provider/chat/browser flows and source-conditioned crop evaluation remain
separate acceptance checks on the final combined deployment.
