import { expect, type Page, test } from "@playwright/test";

// Against the REAL agent services through the Vite proxy (no mocks). Opt-in:
//   AGENT_AGENT=deterministic AGENT_PORT=8000 uv run python -m agent_service
//   AGENT_AGENT=scripted-llm AGENT_PORT=8001 uv run python -m agent_service
//   FS_E2E_LIVE=1 npx playwright test e2e/live.spec.ts
// Also writes the README screenshots (docs/screenshots).
test.skip(!process.env.FS_E2E_LIVE && !process.env.FS_E2E_OFFLINE, "set FS_E2E_LIVE=1 with both backends running");

const shots = "docs/screenshots";
const launcher = (page: Page) => page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$|^(Open|Close) Field Assistant$/ });
const ask = async (page: Page, text: string) => {
  const box = page.getByRole("textbox", { name: /Повідомлення помічнику|Message to the assistant/ });
  await box.fill(text);
  await box.press("Enter");
};

test("live: rules agent - field facts, constraint confirmation, map action, simulation refusal", async ({ page }) => {
  await page.goto("/?host=full");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${shots}/desktop-launcher.png` });
  await launcher(page).click();
  await expect(page.getByText("Підключено")).toBeVisible();
  await page.screenshot({ path: `${shots}/desktop-welcome.png` });

  await page.getByRole("button", { name: "Що відомо про моє поле?" }).click();
  await expect(page.getByText(/Ось що відомо про це поле/)).toBeVisible();

  await ask(page, "Поливу немає. Хочу менше витрачати на добрива."); // the suggestion's words (suggestions show only before the first question)
  const confirm = page.getByRole("button", { name: /^Підтвердити умову:/ });
  await expect(confirm).toBeVisible();
  await page.screenshot({ path: `${shots}/desktop-constraint-proposed.png` });
  await confirm.click();
  await expect(page.locator(".fsa-constraint--confirmed")).toBeVisible();

  await ask(page, "Покажи, скільки вологи в ґрунті.");
  await expect(page.getByText("Застосовано автоматично")).toBeVisible();
  await expect(page.getByTestId("layer-l_smap_rootzone")).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: `${shots}/desktop-map-action.png` });

  await ask(page, "Порахуй урожайність кукурудзи під час посухи.");
  await expect(page.getByText(/нічого не розраховував/).last()).toBeVisible();
  await page.screenshot({ path: `${shots}/desktop-simulation-unavailable.png` });
});

test("live: scripted LLM loop - FAO-56 search with verified citations", async ({ page }) => {
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await page.getByRole("button", { name: "Що таке водний стрес у рослин?" }).click();
  const marker = page.getByRole("button", { name: /^Джерело 1:/ });
  await expect(marker).toBeVisible({ timeout: 25_000 });
  await page.getByText(/^Джерела \(/).click();
  await page.screenshot({ path: `${shots}/desktop-citations.png` });
  await marker.click();
  await expect(page.getByRole("region", { name: "Джерело 1" })).toBeVisible();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${shots}/desktop-citation-detail.png` });
});

// M5: scripted agent (:8001) with AGENT_SIM_PROVIDER=http and AGENT_GEO_PROVIDER=http → contract stub (:8100),
// and the demo host fetching the result by id from the same stub. Proves the HTTP path end to end; the stub
// is NOT member 2's simulation service and its numbers are synthetic.
test("live: rotation comparison over HTTP via the contract stub, opened by result reference", async ({ page }) => {
  await page.goto("/?host=full&backend=scripted");
  await expect(page.getByTestId("profile-status")).toContainText("stub");
  await expect(page.getByTestId("revision")).toHaveText("ред. 3"); // same data from the stub: no revision change
  await launcher(page).click();
  await expect(page.locator(".fsa-root").getByText(/Порівняння сівозмін: тестовий режим, результати вигадані/)).toBeVisible();
  await ask(page, "Порівняй сівозміни для мого поля.");
  await expect(page.getByText(/вигадані для перевірки/).first()).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${shots}/desktop-comparison-answer.png` });
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
  await expect(page.getByTestId("comparison")).toBeVisible();
  await expect(page.getByTestId("comparison")).toContainText("fieldshift-contract-stub");
  await page.getByTestId("comparison").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${shots}/desktop-comparison-panel.png`, fullPage: true });
});

// M6C canonical acceptance, full multi-tool conversation (the server half is `uv run python -m evals.acceptance`).
// Real widget → Vite → dev reference host backend `/agent-bff` (signs each request; dev/referenceBff.ts) → agent
// on :8002 that REQUIRES signed requests (scripted model, real loop and validation) → contract stub on :8100 over
// HTTP. Start :8002 with AGENT_API_KEY / AGENT_CONTEXT_SIGNING_KEY equal to FS_BFF_AGENT_KEY / FS_BFF_SIGNING_KEY
// given to `npm run dev` (README › M6C). NOT a real external end-to-end test: no teammate service is involved.
const CANONICAL = "У мене немає поливу. Покажи вологість ґрунту і порівняй дві сівозміни під час посухи.";
test("live: canonical multi-tool conversation through the signing host backend", async ({ page, request }) => {
  test.setTimeout(90_000);
  // The agent behind the host backend refuses anything that did not come through it.
  expect((await request.post("http://127.0.0.1:8002/v1/agent/stream", { data: {} })).status()).toBe(401);

  await page.goto("/?host=full&backend=signed");
  await expect(page.getByTestId("field-select")).toHaveValue("demo-poltava-01");
  await expect(page.getByTestId("revision")).toHaveText("ред. 3");
  await launcher(page).click();
  await expect(page.getByText("Підключено")).toBeVisible();
  await ask(page, CANONICAL);
  const root = page.locator(".fsa-root");
  await expect(root.getByText(/порівняння сівозмін не розраховано/).first()).toBeVisible({ timeout: 25_000 });
  // constraint: already confirmed for Poltava, nothing re-proposed or saved
  await expect(root.getByText(/вже підтверджено для поля/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Підтвердити умову:/ })).toHaveCount(0);
  // facts keep their provenance; the ML estimate is never an observation
  await expect(root.getByText(/супутник NASA SMAP/).first()).toBeVisible();
  await expect(root.getByText(/неперевірена оцінка/).first()).toBeVisible();
  // the map action is applied by the host, which bumps the revision
  await expect(page.getByTestId("layer-l_smap_rootzone")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("revision")).toHaveText("ред. 4");
  // drought is not offered by the service: no comparison, no numbers, an explicit choice instead
  await expect(page.getByTestId("comparison")).toHaveCount(0);
  await expect(root.getByText(/вигадані для перевірки/)).toHaveCount(0);
  const offer = page.getByRole("button", { name: "Порівняй сівозміни для варіанту: типові умови." });
  await expect(offer).toBeVisible();
  await page.screenshot({ path: `${shots}/m6c-1-drought-unavailable.png` });

  // the farmer explicitly chooses the other scenario
  await offer.click();
  await expect(root.getByText(/вигадані для перевірки/).first()).toBeVisible({ timeout: 25_000 });
  await expect(root.getByText(/варіант «типові умови»/).first()).toBeVisible(); // Task 13: display name, not the id
  await expect(root.getByText(/«typical»/)).toHaveCount(0);
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
  const card = page.getByTestId("comparison");
  await expect(card).toHaveAttribute("data-scenario", "typical"); // the result shown is the scenario actually run
  await expect(card.locator("h2")).toContainText("Типові умови");
  await expect(card).toContainText("fieldshift-contract-stub");
  await expect(card).toContainText("4"); // computed for the current revision
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${shots}/m6c-2-typical-comparison.png`, fullPage: true });

  // another field: the Poltava result and chips do not follow
  await page.getByTestId("field-select").selectOption("demo-mykolaiv-01");
  await expect(card).toHaveCount(0);
  const conditions = page.getByTestId("conditions").locator('li[data-status="confirmed"]');
  const before = await conditions.count();
  const revision = await page.getByTestId("revision").textContent();
  await ask(page, CANONICAL);
  const confirm = page.getByRole("button", { name: /^Підтвердити умову:/ });
  await expect(confirm).toBeVisible({ timeout: 25_000 });
  await expect(root.getByText(/карту не змінюю/)).toBeVisible(); // Mykolaiv has no moisture layer
  await expect(conditions).toHaveCount(before); // a proposal is not saved
  await expect(page.getByTestId("revision")).toHaveText(revision ?? "");
  await page.screenshot({ path: `${shots}/m6c-3-constraint-proposed.png` });
  await confirm.click();
  await expect(conditions).toHaveCount(before + 1);
  await expect(page.getByTestId("revision")).not.toHaveText(revision ?? "");
  const state = {
    field: await page.getByTestId("field-select").inputValue(),
    revision: await page.getByTestId("revision").textContent(),
    confirmedConditions: await conditions.count(),
    actionLog: await page.getByTestId("action-log").textContent(),
  };
  await test.info().attach("final-browser-state.json", { body: JSON.stringify(state, null, 2), contentType: "application/json" });
  console.log("final browser state:", JSON.stringify(state));
});

// Task 11: the whole demo in ONE session on ONE backend (`?backend=signed`: reference host backend → scripted agent
// :8002 → contract stub :8100). Scripted model, synthetic data: never presented as Claude or as live NASA data.
test("live: unified demo journey on one backend and one session", async ({ page }) => {
  test.setTimeout(150_000);
  const root = page.locator(".fsa-root");
  const shot = (name: string, fullPage = false) => page.screenshot({ path: `${shots}/demo-${name}.png`, fullPage });
  const revision = page.getByTestId("revision");

  await page.goto("/?host=full&backend=signed");
  await expect(page.getByTestId("field-select")).toHaveValue("demo-poltava-01");
  await expect(revision).toHaveText("ред. 3");
  await page.waitForTimeout(400);
  await shot("1-launcher");

  // 1. general field question
  await launcher(page).click();
  await expect(page.getByText("Підключено")).toBeVisible();
  await expect(root.getByText("Відповіді заготовлені заздалегідь, це не ШІ.")).toBeVisible(); // honest about the model
  await ask(page, "Що відомо про моє поле?");
  // sources and periods sit under the answer; the estimate is still marked in the text
  await expect(root.getByText(/Використані дані/).first()).toBeVisible({ timeout: 25_000 });
  await expect(root.getByText(/неперевірена оцінка/).first()).toBeVisible();
  await shot("2-field-facts");

  // 2. soil moisture: the host applies the published SMAP layer and bumps the revision
  await ask(page, "Покажи вологість ґрунту.");
  await expect(page.getByTestId("layer-l_smap_rootzone")).toHaveAttribute("aria-pressed", "true", { timeout: 25_000 });
  await expect(revision).toHaveText("ред. 4");
  await expect(root.getByText("Застосовано автоматично").last()).toBeVisible();
  await shot("3-map-layer");

  // 3. FAO-56 knowledge with a verified citation (Chapter 8, no weak-relevance warning)
  await ask(page, "Що каже посібник ФАО про водний стрес (нестачу води для рослин)?");
  const marker = page.getByRole("button", { name: /^Джерело 1:/ }).last();
  await expect(marker).toBeVisible({ timeout: 25_000 });
  await expect(root.getByText(/Низька впевненість/)).toHaveCount(0);
  await marker.click();
  const detail = page.getByRole("region", { name: "Джерело 1" });
  await expect(detail).toContainText(/water stressed|water stress/);
  await page.waitForTimeout(300);
  await shot("4-fao-citation");
  await page.keyboard.press("Escape"); // the farmer closes the source detail
  await expect(detail).toHaveCount(0);

  // 4. a standalone farmer condition: proposed, saved only on confirmation
  const confirmed = page.getByTestId("conditions").locator('li[data-status="confirmed"]');
  const before = await confirmed.count();
  await ask(page, "Поливу немає, хочу зменшити витрати на добрива.");
  const confirm = page.getByRole("button", { name: /^Підтвердити умову:/ }).last();
  await expect(confirm).toBeVisible({ timeout: 25_000 });
  await expect(root.getByText(/вже підтверджено для поля/).last()).toBeVisible(); // irrigation: already known
  await expect(confirmed).toHaveCount(before);
  await expect(revision).toHaveText("ред. 4");
  await shot("5-constraint-proposal");
  await confirm.click();
  await expect(confirmed).toHaveCount(before + 1);
  await expect(revision).toHaveText("ред. 5");

  // 5. drought requested: not offered by the service, nothing invented, an explicit choice instead
  await ask(page, "Порівняй дві сівозміни під час посухи.");
  await expect(root.getByText(/порівняння сівозмін не розраховано/).last()).toBeVisible({ timeout: 25_000 });
  await expect(page.getByTestId("comparison")).toHaveCount(0);
  const offer = page.getByRole("button", { name: "Порівняй сівозміни для варіанту: типові умови." }).last();
  await expect(offer).toBeVisible();
  await shot("6-drought-unavailable");

  // 6. the farmer confirms the alternative: the panel shows exactly the scenario that ran, for this revision
  await offer.click();
  await expect(root.getByText(/вигадані для перевірки/).last()).toBeVisible({ timeout: 25_000 });
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).last().click();
  const card = page.getByTestId("comparison");
  await expect(card).toHaveAttribute("data-scenario", "typical");
  await expect(card.locator("h2")).toContainText("Типові умови");
  await expect(card).toContainText("розраховано для ред. 5"); // computed for the current revision
  await card.scrollIntoViewIfNeeded();
  await shot("7-typical-comparison", true);

  // 7. another field: nothing from Poltava follows
  await page.getByTestId("field-select").selectOption("demo-mykolaiv-01");
  await expect(card).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Відкрити порівняння сівозмін/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Підтвердити умову:/ })).toHaveCount(0);
  await expect(root.getByText(/Привіт! Я допоможу/)).toBeVisible();
  await expect(page.getByTestId("action-log")).toHaveCount(0); // the host log shows only Mykolaiv's (none yet)
  await page.waitForTimeout(300);
  await shot("8-field-switch");
  console.log("journey final state:", JSON.stringify({
    field: await page.getByTestId("field-select").inputValue(), revision: await revision.textContent(),
    logHeading: await page.locator(".demo-card--log h2").textContent(),
  }));
});

test("live: phone layout, English", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?host=full&lang=en");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${shots}/mobile-launcher.png` });
  await launcher(page).click();
  await ask(page, "Show soil moisture.");
  await expect(page.getByText("Applied automatically")).toBeVisible();
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${shots}/mobile-conversation.png` });
});

// Run separately with the rules backend STOPPED: FS_E2E_OFFLINE=1 npx playwright test e2e/live.spec.ts -g offline
test("live: backend offline (the real Vite proxy, nothing on :8000)", async ({ page }) => {
  test.skip(!process.env.FS_E2E_OFFLINE, "stop the backend on :8000 and set FS_E2E_OFFLINE=1");
  await page.goto("/?host=full");
  await launcher(page).click();
  await expect(page.getByText("Помічник недоступний")).toBeVisible();
  await expect(page.getByRole("alert").first()).toContainText("Немає зв’язку");
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${shots}/desktop-offline.png` });
});
