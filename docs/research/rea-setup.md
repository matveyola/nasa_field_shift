# REA: локальне підключення та перевірка

Перевірено 10 листопада 2026 року. REA встановлений як окремий дослідницький інструмент; залежності Field Shift, Docker-образи й код продукту не змінювалися. Комітів немає.

## Що встановлено

| Компонент | Стан |
| --- | --- |
| `rea-agents` | Версія `6.8.0`, локально в `.omc/tools/rea/node_modules/rea-agents`; інсталяція з вимкненими npm scripts |
| Node.js | Портативний `24.22.1` у `.omc/tools/rea/runtime/node.exe`; потрібен для заявленого REA діапазону версій. Системний Node не замінювали |
| Codex MCP | Реєстрація в `C:/Users/Nazar Latko/.codex/config.toml`, абсолютні шляхи до локального Node та REA; інші налаштування збережені |
| Резервна копія конфігурації | `C:/Users/Nazar Latko/.codex/config.toml.rea.backup` |
| Skill | `C:/Users/Nazar Latko/.agents/skills/reverse-engineer-anything`, версія інструкцій `49` |

REA потребує, щоб ці локальні файли залишалися на місці. Перенесення репозиторію або видалення `.omc/tools/rea` зламає поточні абсолютні шляхи MCP. `.omc` не є залежністю продукту та не має потрапляти в його образи або реліз.

## Що реально перевірено

- `doctor --client codex`: `healthy: true`, Codex registration `aligned`, skill `aligned`.
- Окремий справжній stdio MCP-клієнт виконав `initialize` та `tools/list`: сервер `rea`, версія `6.8.0`, **229 інструментів**, зокрема `list_browser_targets`, `inspect_web_page`, `observe_web_session`, `capture_browser_scenario`. Процес завершився з кодом `0`.
- REA CLI знайшов авторизовану вкладку OneSoil та успішно виконав пасивний `inspect-web-page`.
- Після виправлень REA також виконав **11 кроків read-only сценарію OneSoil**: відкриття Demo-поля, Status, Field report, Prescription maps, Data та Yield Analysis. Усі кроки мають `status: completed`, cleanup — `disconnected-external`.
- `capture-browser-scenario` успішно виконав контрольний сценарій на окремій локальній HTML-сторінці в Chromium без заданого при запуску `locale`. Результат `ok: true`, код `0`, cleanup `disconnected-external`. Після перевірки тестовий браузер і HTTP-сервер закрито, порт тестового CDP `9225` закритий.

MCP handshake незалежного перевіряльного клієнта **не означає**, що інструменти вже приєднані до поточного чату Codex. У цьому чаті їх ще немає: потрібне перепідключення MCP або перезапуск Codex. Дослідження через CLI працює вже зараз.

`environment_healthy: false` у doctor стосується відсутніх необов’язкових native-аналізаторів та реєстрацій інших клієнтів. Для поточного браузерного дослідження не встановлювали Hopper, Ghidra, IDA, JADX чи Kuna. Це не виправляло б описану нижче помилку.

## Виявлене обмеження сценарію в браузері OneSoil

Дослідницький Python launcher створив persistent Chromium з `locale='en-US'`. REA при підключенні створює власну CDP-сесію й викликає `Emulation.setLocaleOverride`. Chromium відхиляє повторний override з іншої сесії:

```text
Protocol error (Emulation.setLocaleOverride): Another locale override is already in effect
```

Цю несумісність відтворено на **окремому локальному тесті**, не в акаунті OneSoil:

| Початковий browser context | Однаковий REA сценарій |
| --- | --- |
| Явний `locale='en-US'` | `execution_failure`, код `1` |
| Без явного `locale` | `ok: true`, код `0` |

Це сильне підтвердження причини збою на наявному дослідницькому браузері; прихований первинний exception конкретного OneSoil-запуску REA не повернув. У його результаті є також `cleanup_incomplete` без переліку ресурсів. Не слід називати той невдалий сценарій виконаним або робити висновки про кліки за його результатом.

Виправлення застосовано: у `.omc/tools/rea/start-onesoil-browser.py` прибрано явний `locale`; основний агент акуратно перезапустив дослідницький браузер із тим самим локальним профілем. Авторизація збереглася. Пасивний CDP-аналіз цього override не потребував і працював до виправлення.

Після перезапуску виникла інша помилка ініціалізації. Її встановлено окремим викликом REA initialization без навігації; cleanup підтверджено:

```text
frame.evaluate: DOMException: Failed to get ServiceWorkerRegistration objects: The document is in an invalid state.
```

REA за замовчуванням блокує service workers і перевіряє реєстрації в кожному iframe. На одному з фреймів OneSoil ця перевірка падала; який саме iframe — не встановлювали. У сценарії виставлено `environment.service_workers: "allow"`, щоб зберегти звичайну поведінку застосунку; реєстрації не видаляли, захист акаунта не змінювали. Перезапуск браузера для цього не знадобився.

Наступна спроба створила capture, але зупинилася на неточній назві посилання: REA використовує точне accessibility name, включно з alt-текстом іконок. Локатор виправлено за реально записаним accessibility tree. Повторний сценарій завершив усі 11 кроків. Жоден із цих кроків не зберігає поле, не створює VRA чи платний розрахунок.

Фінальний OneSoil capture містить network events, URL та accessibility після кроків. REA позначив загальну completeness як `incomplete`, `missing_sections: [events]`, без truncation: це не повний запис усіх сімейств подій і не підстава заявляти повну поведінкову еквівалентність. Статус кожної дії перевірено окремо, а не лише за зовнішнім `ok: true`.

Контрольний успішний сценарій навмисно не записував events: REA позначив загальну completeness як `incomplete`, `missing_sections: [events]`. Це достатньо для перевірки запуску та cleanup, але не для порівняння поведінки застосунків або мережевого аналізу.

## Повторювані команди

Запускати PowerShell з кореня `C:/Users/Nazar Latko/Desktop/farmers-intelligence`:

Після завершення дослідження браузер акуратно закрито, CDP-порт `9224` перевірено як закритий. Для нового дослідження спочатку запустити `python .omc/tools/rea/start-onesoil-browser.py --visible` в окремому терміналі. Локальний профіль збережений; він не має публікуватися. Команди нижче потребують запущеного браузера для discovery, але doctor працює незалежно.

```powershell
& '.omc/tools/rea/runtime/node.exe' '.omc/tools/rea/node_modules/rea-agents/scripts/rea.mjs' doctor --client codex --format json

& '.omc/tools/rea/runtime/node.exe' '.omc/tools/rea/node_modules/rea-agents/scripts/rea.mjs' list-browser-targets http://127.0.0.1:9224 --format json
```

Target ID брати зі свіжого `list-browser-targets`; після перезапуску вкладки він може змінитися. CDP endpoint має бути локальним і належати спеціально відкритому дослідницькому браузеру. Для аналізу не потрібні копіювання cookies, токенів, `.env` або browser profile у звіт.

## Локальні докази

Вони залишені в ігнорованій `.omc`, не в коді продукту:

- `.omc/tools/rea/setup-result.json` — застосоване налаштування.
- `.omc/tools/rea/doctor-verified.json` — незалежна перевірка реєстрації.
- `.omc/tools/rea/mcp-verification.json` — реальний MCP handshake та список назв browser tools.
- `.omc/tools/rea/locale-fixture-verification.json` — точний CDP exception на ізольованому тесті.
- `.omc/tools/rea/fixture/comparison.json` та два `*-evidence.json` — порівняння REA з явним locale і без нього.
- `.omc/research/onesoil/browser-targets.json`, `passive-entry-evidence.json`, `add-fields-evidence.json` — результати конкретного дослідження, включно з невдалим сценарієм.
- `.omc/tools/rea/session-diagnosis.json` — точний exception перевірки service worker; initialization без навігації, cleanup успішний.
- `.omc/research/onesoil/demo-readonly-scenario.json` — виправлений сценарій.
- `.omc/research/onesoil/demo-rea-verified-evidence.json` — частковий capture з невдалим локатором, не доказ виконання наступних кроків.
- `.omc/research/onesoil/demo-rea-final-evidence.json` — фінальний capture, усі 11 кроків виконано; Evidence ID `ev_a8ea00471a4ea0a5900e325278d7ef0248b3afed476ef13fc206589793c8b99e`.

Офіційні довідкові матеріали: [REA installation](https://rea.tools/get-started/), [browser investigation](https://rea.tools/guides/browser/), [REA repository](https://github.com/morluto/rea). Поведінку override додатково звірено з установленим `dist/browser/PlaywrightScenarioBrowser.js`; інструмент REA не модифікували.
