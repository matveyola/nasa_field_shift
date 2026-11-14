# Python packaging and import boundaries

Production processes run installed distributions. Working-directory changes must
not change which application code or immutable resources they load.

| Distribution | Imports | Ownership |
| --- | --- | --- |
| `field-shift-contracts` | `field_shift_contracts` | Planning validation models; frozen wire contracts |
| `field-shift-science` | `field_shift_science` | Shared domain calculations and versioned inference artifacts |
| `field-shift-api` | `field_shift` | HTTP, permissions, persistence, jobs and domain orchestration |
| `field-shift-models` | `field_shift_models` | Crop engines, bounded evaluation and offline experiments |
| `field-shift-ml` | `field_shift_ml` | Offline acquisition, training and evaluation workflows |
| `field-shift-crop-runtime` | `crop_runtime` | Private authenticated model process |
| `field-shift-agent` | `agent_service`, `contracts`, `integration_stub`, `evals` | Private assistant, its own contracts and evaluation tools |

The agent's existing `contracts` namespace and planning's `field_shift_contracts`
are distinct. Do not make the model or ML environment depend on the complete API.
Shared scientific modules retain their subdomain ownership; application policy
and persistence stay in API modules. Artifacts moved into their owning science
subdomains without changing bytes or release gates.

## Rules for implementation

1. Every Python project declares a build backend and explicit runtime dependencies.
   Importing another project's first-party package requires a declared dependency;
   dev groups do not supply production dependencies. Optional imports require an
   optional extra with an explicit supported workflow.
2. Keep consumer locks independent. ML currently uses NumPy 1.26; API/models use
   NumPy 2. Resolve local packages through `tool.uv.sources`, with versions in
   standard dependency metadata. Editable development installs are allowed;
   production images and acceptance tests use non-editable installations.
3. Do not set `PYTHONPATH`/`PYTHONHOME`, mutate `sys.path`, call `site.addsitedir`,
   add pytest `pythonpath`, or inject sibling `src` directories. This includes CI,
   Docker, `conftest.py` and test helpers. Declare/install the dependency instead.
4. Ship immutable JSON/schema/model files as package data and read them through
   `importlib.resources`. Never locate runtime data by walking to a repository
   root. Repository-authoring commands take an explicit checkout/output path.
5. Mutable downloads, indexes and reports live outside installed packages.
   Agent storage uses an absolute `AGENT_DATA_DIR`; `AGENT_RAG_INDEX_PATH` overrides
   must resolve to an absolute path. Relative explicit input/output paths belong
   to caller-controlled CLI workflows, not hidden runtime defaults.
6. Preserve numerical behavior, frozen wire schemas and artifact bytes during
   packaging changes. Source-bound model identities legitimately change when
   imports change: keep old reports, record a new version and compare numerical
   outputs. Never falsify an old fingerprint to make a benchmark pass.

See uv's [dependency declarations](https://docs.astral.sh/uv/concepts/projects/dependencies/)
and [independent projects](https://docs.astral.sh/uv/concepts/projects/workspaces/).
A path in a build resolver is an explicit package source, not an import-path override.

## Required verification

Run `python3 infra/check_python_packaging.py` (also included in tooling/fast checks).
It checks seven build backends, ordinary first-party imports and path overrides.
Its limitations are explicit: dynamic imports and runtime behavior need execution.

After changing package boundaries, dependency metadata, package data, entry points
or Docker installation, run:

```bash
python3 infra/verify_python_wheels.py --output /tmp/field-shift-wheel-check
```

This builds all seven wheels, checks ownership collisions, installs resolved
runtime dependencies into separate temporary environments constrained by consumer
locks, runs `uv pip check`, then imports and reads resources from an unrelated
working directory with `python -I`. It executes a real bounded crop evaluation,
loads all offline workflow modules, checks the ML NumPy pin and confirms that the
public API did not leak into ML/model environments. It does not train models or
contact paid inference providers. CI requires this job before its aggregate check.

Also run affected tests and the fast suites, then impacted Docker/live integration
checks. For the private assistant's full local RAG tests, prepare the allowlisted
knowledge cache and set `AGENT_DATA_DIR` to its absolute data directory; missing
optional embeddings remain a reported omission. No test skip counts as a pass.

A passing editable checkout alone is insufficient evidence. Record the exact
revision, commands, wheel and integration results, skips and limitations. Source
changes after a wheel build require a fresh wheel build/install; do not trust an
old same-version wheel cached from earlier source.
