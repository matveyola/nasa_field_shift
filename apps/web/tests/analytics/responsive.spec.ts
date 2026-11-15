import { test, expect } from '@playwright/test'

for (const width of [320, 360, 390, 430, 768, 1024, 1440, 1920]) test(`compact section navigation retains context at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.route('**/api/**', route => new URL(route.request().url()).pathname.startsWith('/api/')
    ? route.fulfill({ json: null }) : route.continue())
  await page.goto('/tests/analytics/index.html?section=soil')
  const nav = page.getByRole('navigation', { name: 'Analytics', exact: true })
  await expect(nav.getByRole('button', { name: 'Soil', exact: true })).toHaveAttribute('aria-current', 'page')
  expect((await nav.boundingBox())!.height).toBeLessThanOrEqual(72)
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('two')
  for (const name of ['Overview', 'Water', 'Vegetation', 'Rotations', 'Soil']) {
    const button = nav.getByRole('button', { name, exact: true })
    await button.focus()
    await page.keyboard.press('Enter')
    await expect(button).toHaveAttribute('aria-current', 'page')
    const r = (await button.boundingBox())!, bar = (await nav.boundingBox())!
    expect(r.height).toBeGreaterThanOrEqual(44)
    expect(r.x).toBeGreaterThanOrEqual(bar.x - 1)
    expect(r.x + r.width).toBeLessThanOrEqual(bar.x + bar.width + 1)
    await expect(page.getByRole('combobox', { name: 'Field', exact: true })).toHaveValue('two')
    await expect(page.getByRole('combobox', { name: 'Analysis year', exact: true })).toHaveValue('2026')
    await expect(page.getByRole('combobox', { name: 'Scenario', exact: true })).toHaveValue('dry_year')
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: test.info().outputPath(`compact-${width}.png`), fullPage: true })
})

 test('selected section stays visible when resizing into a phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route('**/api/**', route => new URL(route.request().url()).pathname.startsWith('/api/') ? route.fulfill({ json: null }) : route.continue())
  await page.goto('/tests/analytics/index.html?section=soil')
  const selected = page.getByRole('navigation', { name: 'Analytics' }).getByRole('button', { name: 'Soil', exact: true })
  await expect(selected).toHaveAttribute('aria-current', 'page')
  await page.setViewportSize({ width: 320, height: 900 })
  await expect.poll(async () => {
    const r = (await selected.boundingBox())!
    return r.x >= 0 && r.x + r.width <= 320
  }).toBe(true)
})

for (const width of [320, 390]) test(`Ukrainian header action keeps whole words at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(() => localStorage.setItem('field-shift-language', 'uk'))
  await page.route('**/api/**', route => new URL(route.request().url()).pathname.startsWith('/api/') ? route.fulfill({ json: null }) : route.continue())
  await page.goto('/tests/analytics/index.html?section=water')
  const action = page.locator('.analytics-heading > button')
  await expect(action).toBeVisible()
  const words = await action.evaluate(element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    const result: Array<{ word: string; lines: number }> = []
    let node: Node | null
    while ((node = walker.nextNode())) for (const match of node.textContent!.matchAll(/\S+/gu)) {
      const range = document.createRange()
      range.setStart(node, match.index!); range.setEnd(node, match.index! + match[0].length)
      result.push({ word: match[0], lines: range.getClientRects().length })
    }
    return result
  })
  expect(words.length).toBeGreaterThan(0)
  expect(words.filter(word => word.lines !== 1)).toEqual([])
  expect((await action.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await action.focus(); await page.keyboard.press('Enter')
  await expect(page.getByTestId('action')).toHaveText('agent:one:2026:dry_year:')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
