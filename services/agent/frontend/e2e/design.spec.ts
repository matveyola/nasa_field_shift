import { expect, type Page, test } from "@playwright/test";
import { mockAgent, TEXTS } from "./mockAgent";

// Design walkthrough of the assistant widget (replayed agent answers, no backend needed): every state of the
// redesign is exercised on each run; the review screenshots are written only on request, so routine runs
// leave the PNGs alone: FS_DESIGN_SHOTS=1 npx playwright test e2e/design.spec.ts → docs/screenshots/design/*.png
const snap = (page: Page, options: Parameters<Page["screenshot"]>[0]) =>
  process.env.FS_DESIGN_SHOTS ? page.screenshot(options) : Promise.resolve();

const dir = "docs/screenshots/design";
const launcher = (page: Page) => page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ });
const panel = (page: Page) => page.getByRole("dialog", { name: "Польовий помічник" });
const input = (page: Page) => page.getByRole("textbox", { name: "Повідомлення помічнику" });

async function ask(page: Page, text: string) {
  await input(page).fill(text);
  await input(page).press("Enter");
}

/** Freeze every running CSS animation at `ms` (deterministic frames of the unfold). */
async function freezeAt(page: Page, ms: number) {
  await page.evaluate((t) => document.getAnimations().forEach((a) => { a.pause(); a.currentTime = t; }), ms);
}

async function settle(page: Page) {
  // finish the finite ones; spinners loop forever and cannot be finished
  await page.evaluate(() =>
    document.getAnimations().forEach((a) => a.effect?.getComputedTiming().endTime !== Infinity && a.finish()),
  );
  await page.waitForTimeout(80);
}

test("desktop: launcher, unfold frames, welcome", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await page.waitForTimeout(500);
  await snap(page, { path: `${dir}/01-launcher-closed.png` });
  await snap(page, { path: `${dir}/01b-launcher-detail.png`, clip: { x: 1360 - 150, y: 860 - 150, width: 150, height: 150 } });
  await launcher(page).click();
  for (const t of [0, 100, 200, 300, 420, 560]) {
    await freezeAt(page, t);
    await snap(page, { path: `${dir}/02-unfold-${String(t).padStart(3, "0")}ms.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
  }
  await settle(page);
  await expect(page.getByText("Підключено")).toBeVisible();
  await snap(page, { path: `${dir}/03-welcome.png` });
});

test("desktop: thinking, answers, constraints, actions, evidence", async ({ page }) => {
  await mockAgent(page, { hang: ["Що з посівами після дощу?"] });
  await page.goto("/?host=full");
  await launcher(page).click();
  await settle(page);
  await ask(page, "Що з посівами після дощу?");
  await page.waitForTimeout(1300);
  await snap(page, { path: `${dir}/04-thinking.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
  await page.getByRole("button", { name: "Зупинити" }).click();

  await ask(page, TEXTS.constraints_uk);
  await expect(page.getByRole("button", { name: /^Підтвердити умову:/ }).first()).toBeVisible();
  await page.waitForTimeout(200);
  await snap(page, { path: `${dir}/05-constraint-proposed.png` });
  await page.getByRole("button", { name: /^Підтвердити умову:/ }).first().click();
  await page.waitForTimeout(400);
  await snap(page, { path: `${dir}/06-constraint-confirmed.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });

  await ask(page, TEXTS.map_uk);
  await expect(page.getByText(/Застосовано автоматично/)).toBeVisible();
  await page.waitForTimeout(250);
  await snap(page, { path: `${dir}/07-map-action.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
  await ask(page, TEXTS.field_uk);
  await page.getByText(/Використані дані/).last().click();
  await page.locator(".fsa-evidence").last().evaluate((el) => el.scrollIntoView({ block: "end" }));
  await page.waitForTimeout(250);
  await snap(page, { path: `${dir}/07b-evidence.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
});

test("desktop: FAO-56 citations and source detail", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted");
  await launcher(page).click();
  await settle(page);
  await ask(page, TEXTS.fao_uk);
  await expect(page.locator(".fsa-cite").first()).toBeVisible();
  await page.locator(".fsa-sources summary").click();
  await page.waitForTimeout(250);
  await snap(page, { path: `${dir}/08-citations.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
  await page.locator(".fsa-cite").first().click();
  await expect(page.getByRole("region", { name: /Джерело 1/ })).toBeVisible();
  await page.waitForTimeout(350);
  await snap(page, { path: `${dir}/09-source-detail.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
});

test("desktop: English, comparison chip", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full&backend=scripted&lang=en");
  await page.getByRole("button", { name: "Open Field Assistant" }).click();
  await settle(page);
  await snap(page, { path: `${dir}/10-welcome-en.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
});

test("phone: launcher, unfold, welcome, conversation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAgent(page);
  await page.goto("/?host=full");
  await page.waitForTimeout(500);
  await snap(page, { path: `${dir}/11-phone-launcher.png` });
  await launcher(page).click();
  await freezeAt(page, 240);
  await snap(page, { path: `${dir}/12-phone-unfold-240ms.png` });
  await settle(page);
  await snap(page, { path: `${dir}/13-phone-welcome.png` });
  await ask(page, TEXTS.constraints_uk);
  await expect(page.getByRole("button", { name: /^Підтвердити умову:/ }).first()).toBeVisible();
  await page.waitForTimeout(200);
  await snap(page, { path: `${dir}/14-phone-conversation.png` });
});

test("reduced motion: cross-fade, no rings", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockAgent(page);
  await page.goto("/?host=full");
  await expect(page.locator(".fsa-pulse")).toHaveCount(0);
  await launcher(page).click();
  await expect(panel(page)).toBeVisible();
  await page.waitForTimeout(250);
  await snap(page, { path: `${dir}/15-reduced-motion-open.png`, clip: { x: 1360 - 470, y: 0, width: 470, height: 860 } });
});
