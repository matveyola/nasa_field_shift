import { expect, type Page, test } from "@playwright/test";
import { mockAgent, TEXTS } from "./mockAgent";

// Task 13: the floating assistant must not hide data. Desktop: the open panel never covers the comparison
// table or the header controls. Phones: the launcher never covers the field facts. Screenshots go to
// docs/screenshots/layout/ only when FS_SHOT_TAG is set ("before" / "after"), so routine runs leave them alone.
const launcher = (page: Page) => page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ });
const panel = (page: Page) => page.getByRole("dialog", { name: "Польовий помічник" });
const input = (page: Page) => page.getByRole("textbox", { name: "Повідомлення помічнику" });
const shot = async (page: Page, name: string) => {
  if (process.env.FS_SHOT_TAG) await page.screenshot({ path: `docs/screenshots/layout/${process.env.FS_SHOT_TAG}-${name}.png` });
};

type Box = { x: number; y: number; width: number; height: number; text?: string };
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

/** Where the text itself is drawn (element boxes of grid items stretch across the whole row). */
function textBoxes(page: Page, selector: string): Promise<Box[]> {
  return page.locator(selector).evaluateAll((els) =>
    els.flatMap((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return [...range.getClientRects()]
        .filter((r) => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight)
        .map((r) => ({ x: r.x, y: r.y, width: r.width, height: r.height, text: el.textContent?.trim().slice(0, 40) }));
    }),
  );
}

for (const width of [1280, 1440, 1920]) {
  test(`desktop ${width}px: the open assistant covers no comparison data or header control`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockAgent(page);
    await page.goto("/?host=full&backend=scripted");
    await launcher(page).click();
    await input(page).fill(TEXTS.compare_uk);
    await input(page).press("Enter");
    await page.getByRole("button", { name: /Відкрити порівняння сівозмін/ }).click();
    const card = page.getByTestId("comparison");
    await expect(card).toBeVisible();
    await expect(panel(page)).toBeVisible(); // the assistant stays open and floating
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300); // panel slide-in
    await shot(page, `desktop-${width}`);

    const covers = [(await panel(page).boundingBox())!, (await launcher(page).boundingBox())!];
    const data = await textBoxes(page, "[data-testid=comparison] :is(h2, .demo-pill--warn, .demo-muted, th, td)");
    expect(data.length).toBeGreaterThan(20);
    expect(data.filter((t) => covers.some((c) => overlaps(t, c)))).toEqual([]);
    const controls = await page.locator(".demo-controls :is(select, button)").evaluateAll((els) =>
      els.map((el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, text: el.tagName }; }));
    expect(controls.filter((t) => covers.some((c) => overlaps(t, c)))).toEqual([]);

    // every column fits without sideways scrolling, at a readable size, with scenario and synthetic labels
    await expect(card.locator("thead th")).toHaveCount(6);
    const table = page.getByTestId("comparison-table");
    expect(await table.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await card.locator("td").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(13);
    await expect(card.locator("h2")).toHaveText("Порівняння сівозмін · Типові умови");
    await expect(card).toContainText("Синтетичні тестові результати");
    await table.focus(); // keyboard users can reach (and scroll) the table region
    await expect(table).toBeFocused();

    // closing gives the page its full width back
    await launcher(page).click();
    await expect(panel(page)).toHaveCount(0);
    await expect.poll(async () => (await card.boundingBox())!.width).toBeGreaterThan(width - 100);
  });
}

for (const [width, height] of [[320, 640], [375, 812], [430, 932]] as const) {
  test(`phone ${width}px: the launcher covers no field facts, and chat still opens and closes`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await mockAgent(page);
    await page.goto("/?host=full");
    const facts = page.locator(".demo-facts");
    await expect(facts.locator("li")).toHaveCount(4);
    await page.waitForTimeout(300);
    await shot(page, `mobile-${width}`);
    const factText = ".demo-facts li > *";

    const button = (await launcher(page).boundingBox())!;
    expect(button.x).toBeGreaterThanOrEqual(0);
    expect(button.x + button.width).toBeLessThanOrEqual(width);
    expect(button.y + button.height).toBeLessThanOrEqual(height);
    expect(Math.round(button.width)).toBe(Math.round(button.height)); // still the round launcher

    // where the facts first appear, and with the facts card scrolled to the bottom edge of the screen
    for (const block of ["first", "end"] as const) {
      if (block === "end") await facts.evaluate((el) => el.scrollIntoView({ block: "end" }));
      const at = (await launcher(page).boundingBox())!;
      const seen = await textBoxes(page, factText);
      if (block === "end") expect(seen.length).toBeGreaterThan(0); // on a short phone the facts start below the fold
      expect(seen.filter((t) => overlaps(t, at))).toEqual([]);
    }

    // the end of the page clears the launcher
    await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
    const last = (await page.locator(".demo-card").last().boundingBox())!;
    expect(last.y + last.height).toBeLessThanOrEqual((await launcher(page).boundingBox())!.y);

    await launcher(page).click();
    await expect(panel(page)).toHaveAttribute("aria-modal", "true");
    await page.getByRole("button", { name: "Закрити помічника", exact: true }).click();
    await expect(launcher(page)).toBeFocused();
    await expect(facts).toContainText("Вологість ґрунту біля коренів"); // the catalogue label, not "root zone soil moisture"
  });
}
