# Field Shift — demo freeze (Task 12)

Frozen on top of `f3e08e2` (Task 11). Everything below runs locally with a **scripted demo model and synthetic data**:
no Anthropic API call, no real NASA download, no teammate service.

## 1. Required local services

| Port | Process | Role |
|---|---|---|
| 8100 | `integration_stub` | contract stub standing in for the geo and simulation services (synthetic values, offers only `typical`) |
| 8002 | `agent_service` (scripted-llm, signed requests required) | the one demo backend: agent loop, tools, FAO-56 search, grounding |
| 5173 | Vite dev server (`npm run dev`) | demo host page + dev reference host backend `/agent-bff` that signs every request |

Ports 8000 (rules agent) and 8001 (unsigned scripted agent) are needed only for the full live test suite, not for the demo.

## 2. Windows startup (PowerShell)

Once: install Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node.js; in the repository folder:

```powershell
uv sync; npm --prefix frontend install
```

```powershell
uv run python -m agent_service.knowledge fetch; uv run python -m agent_service.knowledge build --no-embed
```

Generate two keys (different random strings, ≥ 32 characters) and keep them for terminals B and C:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

Terminal A, the contract stub:

```powershell
uv run python -m integration_stub --port 8100
```

Terminal B, the agent (paste the first key as `K1`, the second as `K2`):

```powershell
$env:AGENT_AGENT="scripted-llm"; $env:AGENT_PORT="8002"; $env:AGENT_RAG_MODE="lexical"; $env:AGENT_API_KEY="K1"; $env:AGENT_CONTEXT_SIGNING_KEY="K2"; $env:AGENT_REQUIRE_CONTEXT_ATTESTATION="true"; $env:AGENT_GEO_PROVIDER="http"; $env:AGENT_GEO_URL="http://127.0.0.1:8100/geo"; $env:AGENT_SIM_PROVIDER="http"; $env:AGENT_SIM_URL="http://127.0.0.1:8100/sim"; uv run python -m agent_service
```

Terminal C, the demo page (same two keys):

```powershell
$env:FS_BFF_AGENT_KEY="K1"; $env:FS_BFF_SIGNING_KEY="K2"; npm --prefix frontend run dev
```

Open **http://localhost:5173/?host=full&backend=signed** (the default page is now blank, with only the assistant). Check before presenting: the widget says «Підключено», the services card
says «Профіль поля: отримано з гео-сервісу (stub, 8 показників)». Keys stay in the terminals; never in the browser or Git.

## 3. Five-minute demonstration script

| Time | Say / do | Expected behaviour |
|---|---|---|
| 0:00 | Introduce the page: Poltava selected, «Усі дані синтетичні»; open the round launcher | widget opens: «Підключено», «Приклад», «Числа вигадані. Відповіді заготовлені заздалегідь, це не ШІ.», «ред. 3» |
| 0:30 | «Що відомо про моє поле?» | facts with sources: SMAP `SPL4SMGP v008`, GPM `GPM_3IMERGDF v07`, SoilGrids (ISRIC), NASADEM, HLS; the detailed moisture marked «комп’ютерна оцінка, не вимір; точність ще не перевірена»; every value «демо-дані, не справжній вимір» |
| 1:10 | «Покажи вологість ґрунту.» | water facts; action «Вологість ґрунту (супутник)» «Застосовано автоматично»; the map switches to *smap rootzone*; revision 3 → 4; host log «Журнал дій хоста · Демо-поле під Полтавою» |
| 1:40 | «Що каже посібник ФАО про водний стрес (нестачу води для рослин)?», then click «Джерело 1», then Esc | two quotes from FAO-56 Chapter 8 in the original English with [1] [2]; the source panel shows the exact sentence ("…the crop is said to be water stressed") and the FAO link; no low-confidence note |
| 2:30 | «Поливу немає, хочу зменшити витрати на добрива.» | «Низький бюджет на добрива» card *Запропоновано* with «З ваших слів: …»; irrigation «вже підтверджено»; nothing saved yet. Click **Підтвердити** → condition confirmed, revision 5 |
| 3:10 | «Порівняй дві сівозміни під час посухи.» | «Для варіанту «посуха (як у 2024)» порівняння сівозмін не розраховано.» + the server's question and buttons; no comparison, no numbers |
| 3:40 | click «Порівняй сівозміни для варіанту: типові умови.», then «Відкрити порівняння сівозмін» | answer with «вигадані для перевірки», every value «розрахунок сівозміни, варіант «типові умови»; … демо-дані, не справжній вимір»; the page makes room for the open widget, so the whole table stays visible beside it; panel «Порівняння сівозмін · Типові умови», «Синтетичні тестові результати», «розраховано для ред. 5» |
| 4:30 | switch the field to Mykolaiv | fresh conversation and suggestions; no Poltava chips or panel; «Журнал дій хоста · Демо-точка під Миколаєвом» «Ще немає дій від помічника.» |

If a step misbehaves: «Скинути демо» resets the fields; restarting terminal B clears the agent.

## 4. Synthetic-data disclosures shown on screen

- Page header: «Усі дані синтетичні»; map: «Ілюстративний вигляд шару: значення даних не показуються»; fact sources «… · fixture».
- Widget header: «Приклад», «Числа вигадані. Відповіді заготовлені заздалегідь, це не ШІ.», «Порівняння сівозмін: тестовий режим, результати вигадані».
- Answers: every value «(…; демо-дані, не справжній вимір)»; estimated values «комп’ютерна оцінка, не вимір; точність ще не перевірена»; warning «Це демо-числа для прикладу, а не справжні виміри вашого поля.»; comparisons «Це розрахунок, а не справжній урожай, і числа поки вигадані для перевірки».
- Comparison panel: «Синтетичні тестові результати», «Результат моделювання, не спостереження», engine `fixture-replay`, service `fieldshift-contract-stub (stub)`.
- Services card: «Профіль поля: отримано з гео-сервісу (stub, 8 показників)».

Say aloud as well: the answers come from a scripted demo model, not Claude; the numbers are fixtures shaped like the
products they name; the FAO-56 quotes are real, verified passages.

## 5. Not verified with real integrations

Geo service (member 1), simulation service (member 2), real host app and its backend (members 4, 5: login, field
authorization, rate limiting), a live Claude run (no API key), dense/rerank FAO retrieval in the demo (lexical only), and
any deployment beyond one local process (replay protection is per process).

## 6. Test results at freeze

Task 13: backend 521 · agent evals 108/108 · acceptance 25/25 · widget and demo unit 84 · replayed browser 23 (incl. 6
layout) · live browser 6 (1 opt-in skipped) · `scripts/verify.py` 12/12. Task 13 regression tests: `e2e/layout.spec.ts`
(nothing covered at 1280/1440/1920 and 320/375/430 px), `tests/test_grounding.py` (scenario display names, unknown id).
Task 12 regression tests: `src/demo/ComparisonCard.test.tsx`
(scenario names, unknown-id fallback) and `e2e/integration.spec.ts` "D+F" and "C" (panel heading, field-scoped log).

## 7. Remaining release blockers and known visual issues

Blockers for anything beyond a local demo: real teammate services; real host authentication and field authorization;
live model verification; a shared replay store if more than one agent process runs.

Visual (Task 13 fixed the table under the open widget, the phone launcher over the field facts, the scenario id in
chat values and the English metric names; `e2e/layout.spec.ts` checks 1280/1440/1920 and 320/375/430 px). Still open:
- Between 641 and 1099 px wide the open widget floats over the page as before (the table scrolls sideways there).
- On a 320 px phone the launcher overlaps the end of the map-layer chips until the page is scrolled a little.
- Host-page leftovers in English: the comparison column «Rotation», crop ids and «calculated / ML estimate · fixture»
  in table cells, and constraint types in the farmer-conditions card («irrigation available: false»).
