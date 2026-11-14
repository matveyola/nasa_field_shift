import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { mockAgent, TEXTS } from "./mockAgent";

const launcher = (page: Page) => page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ });
const panel = (page: Page) => page.getByRole("dialog", { name: "Польовий помічник" });
const input = (page: Page) => page.getByRole("textbox", { name: "Повідомлення помічнику" });

async function ask(page: Page, text: string) {
  await input(page).fill(text);
  await input(page).press("Enter");
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
});

test("1-3: opens from the launcher, streams an answer, renders verified field facts", async ({ page }) => {
  const requests = await mockAgent(page);
  await page.goto("/?host=full");
  const button = launcher(page);
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".fsa-pulse > span")).toHaveCount(2);
  const box = await button.boundingBox();
  const vp = page.viewportSize()!;
  expect(box!.x + box!.width).toBeGreaterThan(vp.width - 40); // bottom-right corner
  expect(box!.y + box!.height).toBeGreaterThan(vp.height - 40);

  await button.click();
  await expect(panel(page)).toBeVisible();
  await expect(page.getByText("Відповідає не ШІ, а готові правила.")).toBeVisible();
  await page.getByRole("button", { name: TEXTS.field_uk }).click();

  await expect(page.getByText(/Ось що відомо про це поле/)).toBeVisible();
  expect(requests[0]!.body.field_context).toMatchObject({ field_id: "demo-poltava-01", revision: 3 });
  await expect(page.getByText("Це демо-числа для прикладу, а не справжні виміри вашого поля.")).toBeVisible();
  await page.getByText(/Використані дані/).click();
  await expect(page.locator(".fsa-fact").first()).toContainText("NASA NSIDC DAAC");
});

test("4: shows a scientific citation and its verified source", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await page.getByRole("button", { name: TEXTS.fao_uk }).click();
  const marker = page.getByRole("button", { name: /^Джерело 1:/ });
  await marker.click();
  const detail = page.getByRole("region", { name: "Джерело 1" });
  await expect(detail).toBeVisible();
  await expect(detail.getByText("Оригінальний текст (англійська)", { exact: false })).toBeVisible();
  const link = detail.getByRole("link", { name: /Переглянути оригінальне джерело/ });
  await expect(link).toHaveAttribute("href", /^https:\/\/www\.fao\.org\/4\/x0490e\//);
  await expect(link).toHaveAttribute("target", "_blank");
  await page.keyboard.press("Escape");
  await expect(detail).toBeHidden();
  await expect(marker).toBeFocused();
});

test("5: a constraint changes nothing until the farmer confirms it", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).click();
  await ask(page, TEXTS.constraints_uk);
  const confirm = page.getByRole("button", { name: /^Підтвердити умову:/ });
  await expect(confirm).toBeVisible();
  await expect(page.getByTestId("action-log")).toHaveCount(0);
  await expect(page.getByTestId("revision")).toHaveText("ред. 3");

  await confirm.click();
  await expect(page.locator(".fsa-constraint--confirmed")).toContainText("Підтверджено");
  await expect(page.getByTestId("action-log")).toContainText("compare.set_constraints");
  await expect(page.getByTestId("conditions").locator('li[data-status="confirmed"]')).toHaveCount(2);
  await expect(page.getByTestId("revision")).toHaveText("ред. 4");
});

test("6-7: a map action is applied by the host; a stale chip is rejected", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).click();
  await ask(page, TEXTS.map_uk);
  // auto action: the published SMAP product is shown at once, through the host
  await expect(page.getByText("Застосовано автоматично")).toBeVisible();
  await expect(page.getByTestId("layer-l_smap_rootzone")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("action-log")).toContainText("map.set_layers");

  // the farmer changes the map themselves: the answer's remaining chip is now stale
  await page.getByTestId("layer-l_hls_ndvi").click();
  await page.locator(".fsa-chip").filter({ hasText: /Вологість ґрунту детально/ }).click();
  await expect(page.getByText("Не застосовано: вигляд поля змінився після відповіді. Запитайте ще раз.")).toBeVisible();
  await expect(page.getByTestId("layer-l_sm_30m")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("action-log").locator("li")).toHaveCount(1);
});

test("an unsupported simulation is explained, never invented", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).click();
  await ask(page, TEXTS.simulation_uk);
  await expect(page.getByText(/окрема програма, яку ще не підключено/)).toBeVisible();
  await expect(page.getByText("Частина даних або джерел для цієї відповіді недоступна.")).toBeVisible();
});

test("switching fields mid-answer cancels it; nothing reaches the new field", async ({ page }) => {
  await mockAgent(page, { hang: [TEXTS.map_uk] });
  await page.goto("/?host=full");
  await launcher(page).click();
  await ask(page, TEXTS.map_uk);
  await expect(page.getByRole("button", { name: "Зупинити" })).toBeVisible();
  await page.getByTestId("field-select").selectOption("demo-kansas-01");
  await expect(page.getByText(/Привіт! Я допоможу/)).toBeVisible(); // Kansas: its own, empty conversation
  await expect(input(page)).toBeEnabled();
  await expect(page.getByTestId("action-log")).toHaveCount(0);
  await page.getByTestId("field-select").selectOption("demo-poltava-01");
  await expect(page.getByText(/Зупинено, бо ви перейшли до іншого поля/)).toBeVisible();
});

test("8: closing and reopening keeps the conversation", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).click();
  await ask(page, TEXTS.simulation_uk);
  await expect(page.getByText(/окрема програма/)).toBeVisible();
  await page.getByRole("button", { name: "Закрити помічника", exact: true }).click();
  await expect(panel(page)).toBeHidden();
  await launcher(page).click();
  await expect(page.getByText(/окрема програма/)).toBeVisible();
});

test("offline service: a clear, recoverable error", async ({ page }) => {
  await mockAgent(page, { offline: true });
  await page.goto("/?host=full");
  await launcher(page).click();
  await expect(page.getByText("Помічник недоступний")).toBeVisible();
  await expect(page.getByRole("alert").first()).toContainText("Немає зв’язку з помічником");
  await expect(page.getByRole("button", { name: "Спробувати ще раз" }).first()).toBeVisible();
});

test("10: fully keyboard operable", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).focus();
  await page.keyboard.press("Enter");
  await expect(input(page)).toBeFocused();
  await page.keyboard.type(TEXTS.simulation_uk);
  await page.keyboard.press("Enter");
  await expect(page.getByText(/окрема програма/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel(page)).toBeHidden();
  await expect(launcher(page)).toBeFocused();
});

test("9: phone layout is a full-screen modal dialog", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAgent(page);
  await page.goto("/?host=full");
  await launcher(page).click();
  const dialog = panel(page);
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await page.waitForTimeout(300); // let the 220 ms slide-in finish
  const settled = (await dialog.boundingBox())!;
  expect([settled.x, settled.y, settled.width, settled.height].map(Math.round)).toEqual([0, 0, 390, 844]);
  await expect(launcher(page)).toHaveCount(0);
  await ask(page, TEXTS.constraints_uk);
  const confirm = page.getByRole("button", { name: /^Підтвердити умову:/ });
  const target = (await confirm.boundingBox())!;
  expect(target.height).toBeGreaterThanOrEqual(24); // WCAG 2.2 target size (minimum)
  await page.getByRole("button", { name: "Закрити помічника", exact: true }).click();
  await expect(launcher(page)).toBeFocused();
});

test("reduced motion: no pulse rings", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/?host=full");
  await expect(launcher(page)).toHaveAttribute("data-motion", "reduced");
  await expect(page.locator(".fsa-pulse")).toHaveCount(0);
});

test("theme: names from the earlier palette still steer the light palette", async ({ page }) => {
  await page.goto("/?host=full");
  const resolved = await page.evaluate(() => {
    const root = document.querySelector<HTMLElement>(".fsa-root")!;
    root.style.setProperty("--fsa-mint", "rgb(255, 0, 0)");
    root.style.setProperty("--fsa-emerald", "rgb(0, 0, 255)");
    const css = getComputedStyle(root);
    return [css.getPropertyValue("--fsa-olive").trim(), css.getPropertyValue("--fsa-accent-text").trim(), css.getPropertyValue("--fsa-accent").trim()];
  });
  expect(resolved).toEqual(["rgb(255, 0, 0)", "rgb(255, 0, 0)", "rgb(0, 0, 255)"]);
});

test("axe: no WCAG A/AA violations in the open assistant (real colours)", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await page.getByRole("button", { name: TEXTS.fao_uk }).click();
  await expect(page.getByRole("button", { name: /^Джерело 1:/ })).toBeVisible();
  const results = await new AxeBuilder({ page }).include(".fsa-root").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
});
