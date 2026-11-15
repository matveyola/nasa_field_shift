# Global application QA — 2026-11-15

This checkpoint tests application behavior and source handling, **not global
agronomic accuracy**. The dedicated, normally authenticated farm contains 24
explicitly synthetic rectangles and hypothetical crop choices. It has its own
database, queues and services; the original farm was not modified. Creation and
retry rules are in [the tester guide](global-tester.md). The scenario inventory
and decision boundaries are in [the product brief](../product/value-and-global-scenarios.md).

## Actual execution

| Boundary | Observed result | What it establishes |
| --- | --- | --- |
| Field weather, rain and calendars | 96 authenticated API reads across 24 fields; 44 independent public-provider responses; 24 mobile field selections | Working field scope, source parsing and missing-data paths. This is not forecast skill. |
| Separate Iowa rainfall baseline check | Independently recomputed 7/30-day totals, dry days and the medians of ten complete prior equal-length periods for one field | Arithmetic on those captured inputs. The separate 24-field inventory did not request typical values; it does not establish 24 baseline comparisons. |
| Forecast comparison | Nine probability values differed across four cases between cached and newly acquired provider responses | Different acquisition vintages were retained. These differences were not disguised as parser failures or fitted away. |
| Short water forecast | 17 supported calculations and seven unsupported cases | Supported-contract execution, field/crop identity and balance invariants. Not measured root-zone water accuracy. |
| Handoff model | 19 submitted jobs: 12 succeeded, seven failed after the normal three attempts; five other cases were unsupported before submission | Partial global data/runtime availability. No replacement data or crops were injected. |
| Successful handoff outputs | 120 seasonal rows, 1,440 exact source-value checks; 240 nulls and 243 numerical zeroes retained separately | Public normalization preserves returned quantities, dates and provenance. |
| Catalog during acquisition | 54 reads, including 21 while a job was running; all returned the 13-profile catalog with the expected engine identity | Metadata remains available while real model work occupies its execution slot. |
| Saved conversations | 24 genuine provider conversations, 48 persisted messages | The history viewer displays actual stored replies. Three initial rate-limit failures were preserved; retries respected the ordinary limit. |

All 12 successful handoff jobs correctly withheld recommendation ranking because
qualified land evidence was missing. Their empirical status remains
`not_validated`. Returned weather members were historical analogues, even though
the outer scenario envelope permits mixed sources. Published country-level
economic references are not farmer-supplied finances or field-yield forecasts;
gross margin remained absent without costs.

Rice was unsupported in three cases; an unknown crop and rapeseed without a
sourced forecast calendar account for the other two pre-submission refusals.
The seven failed jobs reported unavailable required public data or private model.
That public error does not distinguish provider, network and source-coverage
causes. In particular, the Antarctic handoff failure is **not** proof of a
validated cold-suitability classifier. Finland produced an actual unmet
reference-flowering-heat diagnostic. No country label supplied either outcome.

## Cold-water interpretation and assistant quality

The real Antarctic weather response included shallow 0–7 cm temperatures around
−42 °C while the reference balance still emitted a numerical water reserve.
That reproduced an interpretation defect: coherent reserve arithmetic does not
establish liquid water availability. The API now returns a typed applicability
warning; current and saved forecast consumers and assistant evidence preserve it
when present. Legacy results without the diagnostic retain unknown applicability. The warning
does not claim that a shallow temperature measures the whole root zone or add an
unvalidated freezing model. Unsupported applicability stays unknown.

Actual agent checks invoked weather, seasonal history, field facts, knowledge
retrieval, budget calculation, advisory constraints and navigation actions. A
supplied 300,000 UAH budget over 40 ha yielded 7,500 UAH/ha; it was identified as
an input, not predicted profit. Advisory proposals were not silently applied.
Unavailable simulation was explained rather than reported as executed.

The initial live weather answer failed completeness, including stripped dates
and labels, although some forecast facts survived. At the subsequent
date-grounding checkpoint `9782f6b`, the answer retained the archive interval but
omitted the forecast after a rejected table and a blocked repeated fact read.
Logs establish the table's schema-validation failure, not its exact malformed
arguments. Both failed receipts are preserved; HTTP 200 and valid response
schemas were not counted as answer-quality success.

The corrected checkpoint `c06a482` passed independent code and installed-package
review, followed by two fresh real-provider turns on the rebuilt isolated
deployment. T01 retained all 41 facts: five actual forecast dates, a five-row
table with 25 numerical cells, dated remaining forecast quantities and the exact
archive interval and sources. G07 retained seven actual facts, including
−42.1 °C soil temperature, its 6 cm reference depth, returned dates and the NAAS
regional threshold. The answer separates the hypothetical sunflower check from
registered barley and preserves the shallow-cold/liquid-water limitations.
Its water prose names the month; the exact five-day water interval remains in
fact evidence, not explicit narrative dates. Both responses still report
redaction/recovery warnings. This is a scoped recovery-quality pass, not proof
that arbitrary model prose is always complete or that the model is calibrated.
The earlier failed runs remain unchanged.

Existing conversations can be opened read-only without importing their evidence
into the currently selected field. New widget turns remain session-local rather
than automatically appearing in saved history; the interface discloses this
limitation. It must not be described as complete conversation persistence.

## Repetition and evidence

Use the ordinary authenticated API and actual selected field/boundary revision.
Keep request IDs, acquisition times, source bytes/checksums, model and parameter
identities, exact crop/calendar inputs, returned jobs and terminal failures in a
private execution directory. Compare values against the same captured vintage;
a fresh provider response can legitimately change. Never overwrite the immutable
seed receipt or an earlier failed run. Credentials, raw responses, images,
database dumps and mutable execution logs are excluded from Git.

Deterministic verification commands are documented in the
[agent test matrix](../../.agents/skills/agent-tree/references/test-matrix.md).
`python3 infra/verify-planning.py` separately exercises isolated real CPU,
PostgreSQL/Redis, apply/replan, restart, fresh-database restore, migrations and
private-service failure. Its synthetic inputs are not empirical labels. Actual
global-provider execution remains separate from deterministic fixtures.

Remaining gaps include seven unexplained availability failures, paddy management,
local land/soil truth, measured crop/soil targets, calibrated uncertainty and
independently validated field yield/profit. The numerical handoff replay verifies
preservation of the teammate's calculations; it does not establish an improvement
in model accuracy. [Global model results](../planning/global-model-results.md)
retain measured skill, negative transfer results and release-gate limitations.
