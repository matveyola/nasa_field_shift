import { expect, test } from "@playwright/test";
import { mockAgent, TEXTS } from "./mockAgent";

// The default demo page is blank: only the assistant. The phone preview runs that page in a phone-sized frame.

test("the default page shows only the assistant, and the full chat works", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/");
  await expect(page.locator(".demo-top, .demo-grid")).toHaveCount(0);
  expect((await page.locator("body").innerText()).trim()).toBe(""); // nothing to read but the launcher
  await page.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ }).click();
  const dialog = page.getByRole("dialog", { name: "Польовий помічник" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".fsa-badge--fixture")).toContainText("Приклад"); // still honest about made-up data
  await expect(dialog.getByText(/^Числа вигадані\./)).toBeVisible();
  await expect(dialog.getByText(/На карті/)).toHaveCount(0); // no map on this page, so no "on the map" line
  await expect(dialog.locator(".fsa-context-rev")).toHaveCount(0); // no technical revision numbers here
  await page.getByRole("button", { name: TEXTS.field_uk }).click();
  await expect(page.getByText(/Ось що відомо про це поле/)).toBeVisible();
});

test("?host=full brings the demo host page back", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/?host=full");
  await expect(page.getByTestId("field-select")).toBeVisible();
});

test("phone preview: a 393 px wide screen where the chat opens full screen", async ({ page }) => {
  await mockAgent(page);
  await page.goto("/phone.html");
  const frame = page.getByTestId("phone-frame");
  await expect(frame).toHaveAttribute("src", "/?lang=uk&backend=rules");
  const inside = page.frameLocator('[data-testid="phone-frame"]');
  await inside.getByRole("button", { name: /^(Відкрити|Закрити): Польовий помічник$/ }).click();
  const dialog = inside.getByRole("dialog", { name: "Польовий помічник" });
  await expect(dialog).toHaveAttribute("aria-modal", "true"); // the phone layout, as on a real device
  expect(await dialog.evaluate((el) => Math.round(el.getBoundingClientRect().width))).toBe(393);

  await page.getByTestId("phone-lang").selectOption("en");
  await expect(frame).toHaveAttribute("src", "/?lang=en&backend=rules");
  await page.getByRole("radio", { name: "Scientific sources" }).check();
  await expect(frame).toHaveAttribute("src", "/?lang=en&backend=scripted");
});
