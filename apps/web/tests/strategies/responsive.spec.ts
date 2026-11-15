import { test, expect, type Page } from '@playwright/test'
import { catalogFixture, jobFixture, resultFixture } from './handoffFixtures'

// Synthetic presentation data only, never a measured agronomic result.
async function prepare(page: Page) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/handoff/catalog')) return route.fulfill({ json: catalogFixture() })
    if (path.includes('/handoff/fields/')) return route.fulfill({ json: route.request().method() === 'POST' ? jobFixture('north', 'succeeded', resultFixture()) : [] })
    return route.fulfill({ status: 503, json: { detail: 'Synthetic unavailable evidence' } })
  })
  await page.goto('/tests/strategies/index.html?long')
  await expect(page.locator('.handoff-plan')).toHaveCount(2)
}
async function contained(page: Page) {
  expect(await page.locator('.strategies-page').evaluate(root => {
    const failures: string[] = []
    for (const element of root.querySelectorAll<HTMLElement>('input,select,button,summary,.handoff-plan,.handoff-outcome,.strategies-workflow')) {
      const rect = element.getBoundingClientRect()
      if (!rect.width || !rect.height) continue
      const owner = element.closest('.handoff-plan,.handoff-outcome,.strategies-context') ?? root
      const bound = owner.getBoundingClientRect()
      if (rect.left < bound.left - 1 || rect.right > bound.right + 1 || rect.left < -1 || rect.right > innerWidth + 1) failures.push(`${element.tagName}: ${element.textContent?.slice(0,70)}`)
    }
    // Text can spill out of a correctly sized button without document overflow.
    for (const element of root.querySelectorAll('.strategies-workflow strong,.strategies-workflow span')) {
      const range = document.createRange()
      range.selectNodeContents(element)
      const bound = element.closest('button')!.getBoundingClientRect()
      for (const rect of range.getClientRects()) if (rect.left < bound.left - 1 || rect.right > bound.right + 1) failures.push(`Text outside workflow: ${element.textContent}`)
    }
    return failures
  })).toEqual([])
}
for (const width of [320, 360, 390, 430, 768, 1024, 1440, 1920]) test(`controls stay inside their cards at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 })
  await prepare(page)
  await page.getByRole('combobox', { name: 'Plans to compare', exact: true }).selectOption('3')
  await page.getByRole('combobox', { name: 'Crop cycles to compare', exact: true }).selectOption('3')
  for (const letter of ['A', 'B', 'C']) for (const cycle of [1, 2, 3]) await page.getByRole('combobox', { name: `Crop profile · ${letter}${cycle}`, exact: true }).selectOption('maize_grain')
  await expect(page.getByRole('combobox', { name: 'Crop profile · A1', exact: true }).locator('option')).toHaveCount(14)
  await page.locator('.handoff-workspace details').evaluateAll(elements => elements.forEach(element => { (element as HTMLDetailsElement).open = true }))
  await contained(page)
  await page.getByRole('button', { name: /^Next sowing opportunities/ }).click()
  await expect(page.getByText('Synthetic unavailable evidence').first()).toBeVisible()
  await contained(page)
  await page.getByRole('button', { name: /^Compare crop plans/ }).click()
  await expect(page.getByRole('combobox', { name: 'Crop profile · C3', exact: true })).toHaveValue('maize_grain')
  await page.screenshot({ path: test.info().outputPath(`responsive-${width}.png`), fullPage: true })
})
test('result cards, focus and doubled rendering remain usable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await prepare(page)
  // Text/control enlargement stress plus a 720px CSS viewport equivalent of desktop zoom.
  // Match the shell's remaining workspace width beside its sidebar at 200%.
  await page.locator('.strategies-page').evaluate(element => { element.style.maxWidth = '416px' })
  await page.locator('body').evaluate(element => { element.style.zoom = '2' })
  await contained(page)
  await page.locator('body').evaluate(element => { element.style.zoom = '' })
  await page.locator('.strategies-page').evaluate(element => { element.style.maxWidth = '' })
  await page.setViewportSize({ width: 720, height: 500 })
  await page.getByLabel('Planting date · 1').fill('2027-05-01')
  for (const letter of ['A', 'B']) {
    const crop = page.getByRole('combobox', { name: `Crop profile · ${letter}1`, exact: true })
    await crop.selectOption('maize_grain')
    await page.locator('.handoff-plan').filter({ has: crop }).getByRole('combobox', { name: 'Growth-duration calendar', exact: true }).selectOption(catalogFixture().crops.find(crop => crop.id === 'maize_grain')!.profile.calendars![0].id)
  }
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.locator('.handoff-outcome')).toHaveCount(2)
  await contained(page)
  await page.getByRole('button', { name: 'Discuss with assistant', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('output')).toHaveText('agent:north:2027')
})

test('every catalog crop and a three-plan result fit narrow cards', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await prepare(page)
  const crop = page.getByRole('combobox', { name: 'Crop profile · A1', exact: true })
  for (const profile of catalogFixture().crops) {
    await crop.selectOption(profile.id)
    await expect(crop).toHaveValue(profile.id)
    await contained(page)
  }
  await page.getByRole('combobox', { name: 'Plans to compare', exact: true }).selectOption('3')
  await page.getByLabel('Planting date · 1').fill('2027-05-01')
  for (const letter of ['A', 'B', 'C']) {
    const choice = page.getByRole('combobox', { name: `Crop profile · ${letter}1`, exact: true })
    await choice.selectOption('maize_grain')
    await page.locator('.handoff-plan').filter({ has: choice }).getByRole('combobox', { name: 'Growth-duration calendar', exact: true }).selectOption(catalogFixture().crops.find(crop => crop.id === 'maize_grain')!.profile.calendars![0].id)
  }
  const result = resultFixture()
  const plans = result.payload.plans as Record<string, unknown>[]
  plans.push({ ...structuredClone(plans[1]), id: 'plan-c' })
  // The additional raw plan has no decision rank; presentation must not manufacture one.
  await page.route('**/handoff/fields/north/jobs', route => route.request().method() === 'POST'
    ? route.fulfill({ json: jobFixture('north', 'succeeded', result) }) : route.fulfill({ json: [] }))
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.locator('.handoff-outcome')).toHaveCount(3)
  await contained(page)
  await expect(page.locator('[data-handoff-plan="plan-c"] h4')).not.toContainText('Rank')
})
