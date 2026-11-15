// Real browser → API → durable worker → private CPU model; no network mocks.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
const { chromium, expect } = createRequire(new URL('../apps/web/package.json', import.meta.url))('@playwright/test')
const baseURL = process.env.PLANNING_LIVE_URL
const output = process.env.PLANNING_LIVE_OUTPUT
if (!baseURL || !output || !process.env.PLANNING_LIVE_PASSWORD) throw new Error('Isolated acceptance environment required')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
for (const mobile of [false, true]) {
const context = await browser.newContext({ baseURL, viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile })
const errors = []
const page = await context.newPage()
page.on('pageerror', e => errors.push(e.message))
try {
  const login = await context.request.post('/api/auth/login', {
    headers: { 'X-Field-Shift-Request': '1', Origin: baseURL },
    data: { email: 'acceptance@example.test', password: process.env.PLANNING_LIVE_PASSWORD },
  })
  assert.equal(login.status(), 200)
  await page.goto('/rotations?desktop=1&planning=1&field=F-01&season=' + new Date().getUTCFullYear())
  const controls = page.locator('.planning-workspace')
  await expect(controls.getByRole('heading', { name: 'Scenario comparison', exact: true })).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('Synthetic demonstration — not measured accuracy', { exact: true })).toBeVisible()
  await controls.getByRole('combobox', { name: 'Field', exact: true }).selectOption('F-02')
  await controls.getByRole('combobox', { name: 'Season', exact: true }).selectOption(String(new Date().getUTCFullYear() + 1))
  await expect(page).toHaveURL(/field=F-02/)
  assert.equal(new URL(page.url()).searchParams.get('season'), String(new Date().getUTCFullYear() + 1))
  await controls.getByRole('button', { name: 'Save and calculate', exact: true }).click()
  await expect(controls.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeEnabled({ timeout: 180000 })
  await expect(controls.getByRole('table')).toBeVisible()
  await expect(controls.getByRole('region', { name: 'Evidence', exact: true })).toContainText('No guaranteed profit')
  await controls.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = controls.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Confirm application', exact: true })).toBeDisabled()
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(page.getByText('Plan applied. Confirmed crops are saved.', { exact: true })).toBeVisible({ timeout: 20000 })
  await controls.getByRole('button', { name: 'Replan', exact: true }).click()
  await expect(controls.getByText('Changes from previous plan', { exact: true })).toBeVisible({ timeout: 180000 })
  await expect(controls.getByText('No assignment changes', { exact: true })).toBeVisible()
  await page.screenshot({ path: output + (mobile ? '/live-mobile.png' : '/live-desktop.png'), fullPage: true })
  await expect(controls.getByRole('heading', { name: 'Scenario comparison', exact: true })).toBeVisible()
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile page overflow')
  await page.goto('/agent?desktop=1&field=F-02&season=2028&trajectory=reference')
  await expect(page).toHaveURL(/\/fields\?/)
  const redirected = new URL(page.url())
  assert.equal(redirected.searchParams.get('field'), 'F-02')
  assert.equal(redirected.searchParams.get('season'), '2028')
  assert.equal(redirected.searchParams.get('trajectory'), 'reference')
  assert.deepEqual(errors, [])
  await writeFile(output + (mobile ? '/browser-mobile.json' : '/browser-desktop.json'), JSON.stringify({ status: 'passed', errors, mockedRequests: 0 }, null, 2))
} catch (error) {
  await page.screenshot({ path: output + '/live-failure.png', fullPage: true })
  await writeFile(output + '/failure-dom.txt', await page.locator('body').innerText())
  throw error
} finally {
  await context.close()
}
}
} finally {
  await browser.close()
}
