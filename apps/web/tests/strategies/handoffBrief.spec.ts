import { test, expect, type Page } from '@playwright/test'
import { catalogFixture, jobFixture, resultFixture } from './handoffFixtures'
import type { HandoffResult } from '../../src/api/rotationHandoff'

async function setup(page: Page, result: HandoffResult, uk = false, ready = true) {
  const posts: unknown[] = []
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/handoff/catalog')) return route.fulfill({ json: { ...catalogFixture(), status: ready ? 'available' : 'unavailable' } })
    if (path.includes('/handoff/fields/')) {
      if (route.request().method() === 'POST') posts.push(route.request().postDataJSON())
      const field = path.split('/')[5]
      if (path.endsWith('/jobs')) return route.fulfill({ json: field === 'north' ? [jobFixture('north', 'succeeded', result)] : [] })
      return route.fulfill({ json: jobFixture(field, 'succeeded', result) })
    }
    return route.fulfill({ status: 404, json: { detail: 'Synthetic unused route' } })
  })
  await page.goto('/tests/strategies/index.html')
  if (uk) await page.getByRole('combobox', { name: /Language|Мова/ }).selectOption('uk')
  return posts
}
async function saved(page: Page, uk = false) {
  await page.getByText(uk ? 'Збережені порівняння для цих меж поля' : 'Saved comparisons for this field boundary', { exact: true }).click()
  await page.getByRole('button', { name: uk ? /^Відкрити збережений розрахунок/ : /^Open saved calculation/ }).click()
  await expect(page.getByRole('heading', { name: uk ? 'Ваше розраховане порівняння' : 'Your calculated comparison' })).toBeVisible()
}
function unranked() {
  const result = resultFixture(false)
  result.suitability_status = 'not_assessed'
  result.diagnostics = [{ code: 'land_suitability_not_assessed', severity: 'blocking', message: 'Synthetic missing land', crop_id: null, start_date: null, end_date: null, details: {} }]
  result.payload.decision = null
  result.payload.weather = { live_source_url: null, member_count: 5 }
  return result
}

test('historical-only saved result has compact checks and keyboard action preserving current draft', async ({ page }) => {
  const posts = await setup(page, unranked())
  await page.getByLabel('Planting date · 1').fill('2028-06-03')
  await page.getByLabel('Crop profile · A1').selectOption('soybeans')
  await saved(page)
  const brief = page.getByRole('complementary', { name: 'Decision brief' })
  await expect(page.getByText('Data basis: Historical weather scenarios for the selected dates')).toBeVisible()
  await expect(page.getByText('Data basis: Short forecast with conditional historical scenarios')).toHaveCount(0)
  await expect(brief).toContainText('Confirm sourced land evidence')
  await expect(brief).toContainText('Equipment or water access checks are absent')
  await expect(brief.getByRole('listitem')).toHaveCount(2)
  await expect(page.getByText('Scenario estimates are available; no crop recommendation is established.', { exact: true })).toHaveCount(1)
  await expect(page.getByText('No recommended plan is established by this result.', { exact: false })).toHaveCount(0)
  await expect(brief.getByRole('button')).toHaveCount(1)
  const action = brief.getByRole('button', { name: 'Review current inputs' })
  await action.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('form', { name: 'Current comparison inputs' })).toBeFocused()
  await expect(page.getByLabel('Planting date · 1')).toHaveValue('2028-06-03')
  await expect(page.getByLabel('Crop profile · A1')).toHaveValue('soybeans')
  expect(posts).toHaveLength(0)
  await expect(page.getByText('First by the selected criterion:', { exact: false })).toHaveCount(0)
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(brief).toHaveCount(0)
})

test('actual mixed source segments are named; source ranks and equal places remain visible', async ({ page }) => {
  const result = resultFixture()
  const decision = result.payload.decision as any; decision.decisions[1].rank = 1
  for (const plan of result.payload.plans as any[]) for (const member of plan.members) for (const cycle of member.seasons) cycle.weather_segments = [
    { source: 'live_forecast', dates: { start: '2027-05-01', end: '2027-05-07' } },
    { source: 'historical_analog', dates: { start: '2027-05-08', end: '2027-08-20' }, historical_dates: { start: '2020-05-08', end: '2020-08-20' } },
  ]
  await setup(page, result); await saved(page)
  await expect(page.getByText('Data basis: Short forecast and historical weather scenarios')).toBeVisible()
  await expect(page.locator('.handoff-ranking')).toContainText('Plan A, Plan B')
  await expect(page.getByRole('complementary', { name: 'Decision brief' })).toContainText('engine-ranked feasible options')
})

test('contradictory cold result never exposes a recommendation or rank', async ({ page }) => {
  const result = resultFixture(); result.suitability_status = 'no_thermal_growth'
  await setup(page, result); await saved(page)
  await expect(page.getByRole('complementary', { name: 'Decision brief' })).toContainText('lacks thermal growth')
  await expect(page.locator('.handoff-ranking')).toHaveCount(0)
  await expect(page.locator('[data-handoff-plan="plan-a"] h4')).not.toContainText('Rank')
})

test('unknown weather and zero revenue with missing cost remain explicit without replacing evidence', async ({ page }) => {
  const result = resultFixture()
  for (const plan of result.payload.plans as any[]) {
    delete plan.members[0].seasons[0].weather_segments
    plan.economics = { gross_revenue_per_ha: 0, gross_revenue_currency: 'USD', gross_margin_per_ha: null, input_origins: ['supplied'] }
  }
  await setup(page, result); await saved(page)
  const brief = page.getByRole('complementary', { name: 'Decision brief' })
  await expect(brief).toContainText('margin is missing')
  await expect(page.getByText('Data basis: Weather coverage is incomplete or unspecified in this result')).toBeVisible()
  await page.locator('[data-handoff-plan="plan-a"]').getByText('Financial and yield inputs', { exact: true }).click()
  await expect(page.locator('[data-handoff-plan="plan-a"]').getByText('0 USD/ha', { exact: true })).toBeVisible()
  await expect(page.getByText('Sources and interpretation', { exact: true })).toBeVisible()
})

test('Ukrainian brief and existing form fit320px with keyboard navigation', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 740 })
  await setup(page, unranked(), true); await saved(page, true)
  const brief = page.getByRole('complementary', { name: 'Коротко про рішення' })
  await expect(brief).toContainText('Підтвердьте дані про землю')
  await expect(page.getByText('Основа даних: Історичні погодні сценарії для обраних дат')).toBeVisible()
  const button = brief.getByRole('button', { name: 'Переглянути поточні дані' })
  await button.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('form', { name: 'Поточні дані порівняння' })).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const rect = await brief.boundingBox(); expect(rect!.width).toBeLessThanOrEqual(320)
  await brief.screenshot({ path: info.outputPath('brief-uk-320.png') })
})


test('without an editable catalog the one action uses only the existing current-field assistant', async ({ page }) => {
  const posts = await setup(page, unranked(), false, false); await saved(page)
  const brief = page.getByRole('complementary', { name: 'Decision brief' })
  await expect(brief.getByRole('button')).toHaveCount(1)
  await expect(brief).toContainText('Discuss the current field’s conditions and missing information')
  await brief.getByRole('button', { name: 'Discuss this field with assistant' }).click()
  await expect(page.locator('output')).toHaveText('agent:north:2027')
  expect(posts).toHaveLength(0)
})
