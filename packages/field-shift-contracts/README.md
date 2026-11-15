# Field Shift planning contracts

Installable `field-shift-contracts` exposes `field_shift_contracts.planning_v1`.
It owns the Python validation models and depends only on Pydantic. API, crop
runtime and offline model tools declare it as a dependency; none imports an API
checkout to obtain contracts.

The HTTP/JSON contract remains version 1.0.0. Frozen schema metadata retains the
original generator location for byte compatibility. Its canonical wire examples
and generated TypeScript remain in `contracts/planning-v1`; the packaged synthetic
example is checked against that source by the export command:

```bash
apps/api/.venv/bin/python -m field_shift_contracts.planning_v1.export --repo-root . --check
```

`--repo-root` is explicit authoring context, not a runtime import path. The command
uses that checkout's locked JSON-to-TypeScript tool. Runtime consumers read the
bundled immutable example through `importlib.resources` when running synthetic
benchmarks; production inference does not substitute it for field data.
