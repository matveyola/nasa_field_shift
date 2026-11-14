import { expect, type Page, test } from "@playwright/test";
import { mockAgent, RESULT_ID, TEXTS } from "./mockAgent";

// M5 flows: profile from the geo service, a synthetic comparison from the simulation service, and the
// host applying (or refusing) a UI action that references a simulation result.
const launcher = (page: Page) => page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ });
const input = (page: Page) => page.getByRole("textbox", { name: "Повідомлення помічнику" });

async function ask(page: Page, text: string) {
  await input(page).fill(text);
  await input(page).press("Enter");
}

test("A: the host loads the field profile from the geo service (synthetic, labelled)", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await expect(page.getByTestId("profile-status")).toContainText("отримано з гео-сервісу (stub, 8 показників)");
  await expect(page.getByTestId("revision")).toHaveText("ред. 3"); // identical data: no revision change
  await expect(page.getByTestId("simulation-status")).toContainText("синтетичні результати");
});

test("D+F: a synthetic comparison is explained, then opened through a verified result reference", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await expect(page.locator(".fsa-root").getByText(/Порівняння сівозмін: тестовий режим, результати вигадані/)).toBeVisible();
  await page.getByRole("button", { name: TEXTS.compare_uk }).click(); // the suggestion button sends its own words

  await expect(page.getByText(/вигадані для перевірки/).first()).toBeVisible();
  await expect(page.getByText(/розрахунок сівозміни, варіант «типові умови»/).first()).toBeVisible();
  await expect(page.getByText(/«typical»/)).toHaveCount(0); // Task 13: the id stays in contracts only
  await page.getByText(/Використані дані/).click();
  await expect(page.locator(".fsa-badge--simulation").first()).toHaveText("Розрахунок, а не вимір");

  await expect(page.getByTestId("comparison")).toHaveCount(0); // nothing shown until the farmer asks for it
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
  const card = page.getByTestId("comparison");
  await expect(card).toBeVisible();
  await expect(card).toContainText(RESULT_ID);
  await expect(card).toContainText("Синтетичні тестові результати");
  await expect(card).toContainText("15% (9–23)");
  // Task 12: a readable scenario name; the id itself is unchanged
  await expect(card.locator("h2")).toHaveText("Порівняння сівозмін · Типові умови");
  await expect(card).toHaveAttribute("data-scenario", "typical");
  await expect(page.getByTestId("action-log")).toContainText("panel.open");
});

test("G: a result the simulation service does not know is refused by the host", async ({ page }) => {
  await mockAgent(page, { resultGone: true });
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await ask(page, TEXTS.compare_uk);
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
  await expect(page.getByText("Не застосовано: у застосунку немає такої карти, графіка чи результату.")).toBeVisible();
  await expect(page.getByTestId("comparison")).toHaveCount(0);
  await expect(page.getByTestId("action-log")).toContainText("unknown_reference");
});

test("E: simulation service down: no comparison offered anywhere", async ({ page }) => {
  await mockAgent(page, { simulationDown: true });
  await page.goto("/?host=full&backend=scripted");
  await expect(page.getByTestId("simulation-status")).toContainText("не підключено");
  await launcher(page).click();
  await expect(page.getByRole("button", { name: "Що відомо про моє поле?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Що посіяти далі? Порівняй сівозміни." })).toHaveCount(0);
  await expect(page.locator(".fsa-root").getByText(/Порівняння сівозмін:/)).toHaveCount(0);
});

test("C: switching fields hides the other field's comparison", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await ask(page, TEXTS.compare_uk);
  await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
  await expect(page.getByTestId("comparison")).toBeVisible();
  await expect(page.getByTestId("action-log")).toContainText("panel.open");
  await page.getByTestId("field-select").selectOption("demo-kansas-01");
  await expect(page.getByTestId("comparison")).toHaveCount(0);
  // Task 12: the host log shows only the selected field's actions, and says which field it is
  await expect(page.getByTestId("action-log")).toHaveCount(0);
  await expect(page.locator(".demo-card--log h2")).toHaveText("Журнал дій хоста · Демо-поле в Канзасі");
  await page.getByTestId("field-select").selectOption("demo-poltava-01");
  await expect(page.getByTestId("comparison")).toHaveCount(0); // shown again only when re-opened
  await expect(page.getByTestId("action-log")).toHaveAttribute("data-field", "demo-poltava-01");
  await expect(page.getByTestId("action-log")).toContainText("panel.open"); // Poltava's own history is kept
});
