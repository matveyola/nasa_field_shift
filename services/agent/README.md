# Field Shift agent runtime

The private Python service handles bounded agent inference, tool calls and evidence grounding. The Field Shift API owns field data and conversations, signs each turn and translates responses for desktop and mobile clients. The standalone widget, examples, evaluation reports and tools are documented in the [runtime reference](REFERENCE.md).

From the repository root, run:

```sh
python infra/configure-agent.py
docker compose --env-file .env --env-file .env.agent --profile agent up --build -d
```

Use `.env.example` as the first env file when there is no local `.env`. The
helper preserves an existing `.env.agent`. The service listens on the private
Compose network and verifies both a bearer credential and a signed field
context. Keep these credentials in `.env.agent` or deployment secrets.

The default `scripted-llm` mode exercises the agent loop, tools and grounding
with a local script. Configure optional Gemini or OpenAI-compatible providers
in the repository root `.env` or the server environment. These providers receive
questions, recent conversation turns and relevant field evidence and may incur
API charges. The browser calls the Field Shift API. [Provider setup](../../docs/assistant-integration.md)

The app uses its saved water and satellite data. The optional external geo and
rotation services and the document index are off in this profile. The included
contract stub and fixtures are for local tests. The host profile is intended
for the local, single-farm workspace.

Install and test from `services/agent`:

```sh
uv sync --locked
uv run --no-sync pytest -q
```

The Python distribution installs `agent_service`, `contracts`, `integration_stub`
and the optional evaluation tools under their existing import names. Contract
schemas, synthetic fixtures and the retrieval benchmark are bundled package data;
tests use installed imports without `PYTHONPATH`. To build a wheel, run
`uv build --wheel`; to exercise the deployment installation locally, run
`uv sync --locked --no-editable`. After installation, the environment's Python can
run `python -I -m agent_service` from any directory. Evaluation commands require
`field-shift-agent[eval]` (or the locked development environment).

Writable files are separate from installed code. Set `AGENT_DATA_DIR` to an
absolute application-data directory. Its default is
`$XDG_DATA_HOME/field-shift-agent` (or `~/.local/share/field-shift-agent`) on Unix
and `%LOCALAPPDATA%/field-shift-agent` on Windows. Knowledge commands use
`AGENT_DATA_DIR/knowledge`; evaluation reports use `AGENT_DATA_DIR/evals/reports`.
`AGENT_RAG_INDEX_PATH` can select a separate mounted index, and the knowledge CLI
accepts an explicit `--data-dir` for ingestion. Directories are created only when
writing, not when importing modules or checking configuration. No documents,
indexes, model caches, credentials or generated reports are included in the wheel.

For the repository's public document cache, explicitly select it before building
a lexical index and running corpus-dependent tests:

```sh
export AGENT_DATA_DIR="$(pwd)/data"
uv run --no-sync python -m agent_service.knowledge build --no-embed
uv run --no-sync pytest -q
```

The raw allowlisted documents must already be present under
`$AGENT_DATA_DIR/knowledge/raw`; `python -m agent_service.knowledge fetch` is the
explicit public-source download step. Optional embeddings still require their
separate dependency group and model cache. Docker installs a non-editable wheel,
uses `/var/lib/field-shift-agent` for writable data and runs from a directory
without application source. It fetches public knowledge only in the build stage.

Schema generation never writes into an installed package implicitly:
`python -m contracts.export --output-dir contracts/schema` selects the repository
output explicitly. `python -m contracts.export --check` checks bundled schemas;
add `--output-dir contracts/schema` to check a separate generated directory.

## Reference widget

The widget includes a standalone React demo with its own npm lockfile. Its
frontend, screenshots and tooling stay outside the agent's Docker build context.
Start in `services/agent` and install the demo and contract tooling dependencies:

```sh
npm ci
npm --prefix frontend ci
npm run check:ts
npm --prefix frontend run build
npm --prefix frontend test
```

To try it with the local rule engine, open two PowerShell terminals in
`services/agent`. In the first terminal, run:

```powershell
$env:AGENT_ENVIRONMENT = 'development'
$env:AGENT_AGENT = 'deterministic'
$env:AGENT_HOST = '127.0.0.1'
$env:AGENT_PORT = '8000'
uv run --locked python -m agent_service
```

In the second terminal, run:

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open [the reference widget](http://127.0.0.1:5173). Its default rule engine uses
fixture data and requires no provider key. For the scripted agent and contract
stub, see the [widget guide](frontend/README.md). Use this demo on your local
machine. The bundled development proxy exists for reference runs.

The [contract exports](CONTRACTS.md), [evaluation cases](evals/) and
[evaluation reports](evals/reports/) describe agent behavior and validation evidence.
The [backup](scripts/backup.py) and [verification](scripts/verify.py) scripts
are utilities for standalone workflows. Run them only in a separate standalone
checkout. Here, Git resolves to the parent monorepo, so the backup script
would bundle the parent repository's committed history.
