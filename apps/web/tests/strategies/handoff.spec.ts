import { test, expect, type Page } from '@playwright/test'
import { catalogFixture, jobFixture, resultFixture } from './handoffFixtures'
import { opportunities } from './opportunityFixtures'
import type { HandoffCatalog, HandoffJob, HandoffResult, HandoffSubmission } from '../../src/api/rotationHandoff'

type Options = { catalog?: HandoffCatalog; result?: HandoffResult; saved?: HandoffJob[]; failFirst?: boolean; failedJob?: boolean; queued?: boolean; wrongScope?: boolean; pollFailure?: boolean }
async function setup(page: Page, options: Options = {}) {
  const posts: HandoffSubmission[] = [], gets: string[] = [], calendarFields: string[] = []
  let polls = 0
  await page.clock.setFixedTime(new Date('2026-11-15T12:00:00Z'))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/planting-opportunities')) {
      const url = new URL(route.request().url()), field = path.split('/')[3]
      calendarFields.push(field)
      return route.fulfill({ json: opportunities(field, url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count'))) })
    }
    if (path.endsWith('/handoff/catalog')) return route.fulfill({ json: options.catalog ?? catalogFixture() })
    if (path.includes('/handoff/fields/')) {
      const field = path.split('/')[5]
      if (path.endsWith('/jobs') && route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON())
        if (options.failFirst && posts.length === 1) return route.abort('failed')
        return route.fulfill({ json: jobFixture(options.wrongScope ? 'other' : field, options.failedJob && posts.length === 1 ? 'failed' : options.queued ? 'queued' : 'succeeded', options.result ?? resultFixture()) })
      }
      gets.push(path)
      if (path.endsWith('/jobs')) return route.fulfill({ json: field === 'north' ? options.saved ?? [] : [] })
      polls++
      if (options.pollFailure && polls === 1) return route.fulfill({ status: 503, json: { detail: 'Synthetic status outage' } })
      return route.fulfill({ json: jobFixture(field, 'succeeded', options.result ?? resultFixture()) })
    }
    if (path === '/api/planning-workspace') return route.fulfill({ json: { farm_id: 'test', profiles: [], missing_profiles: [], request: null, crop_options: [], model_card: null, configuration: null, status: 'unavailable', reason: 'Synthetic missing reviewed inputs' } })
    return route.fulfill({ status: 404, json: { detail: 'Synthetic unused route' } })
  })
  await page.goto('/tests/strategies/index.html')
  await expect(page.getByRole('heading', { name: 'Compare what to grow on this field' })).toBeVisible()
  return { posts, gets, calendarFields }
}
async function fill(page: Page) {
  await page.getByLabel('Planting date · 1').fill('2027-05-01')
  for (const [letter, crop] of [['A', 'maize_grain'], ['B', 'sunflower']]) {
    await page.getByLabel(`Crop profile · ${letter}1`).selectOption(crop)
    const plan = page.locator('.handoff-plan').filter({ has: page.getByLabel(`Crop profile · ${letter}1`) })
    await plan.getByLabel('Growth-duration calendar').selectOption(catalogFixture().crops.find(row => row.id === crop)!.profile.calendars![0].id)
  }
}

test('comparison is primary; all real profiles and explicit calendars precede acquisition', async ({ page }, info) => {
  const { posts } = await setup(page)
  await expect(page.getByRole('button', { name: /^Compare crop plans/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('Crop profile · A1').locator('option')).toHaveCount(14)
  await expect(page.getByLabel('Planting date · 1')).toHaveValue('')
  await page.getByLabel('Crop profile · A1').selectOption('rapeseed_canola')
  await expect(page.getByText('This profile has no sourced catalog calendar.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  expect(posts).toHaveLength(0)
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  expect(posts).toHaveLength(1)
  expect(posts[0]).toMatchObject({ geometry_revision: 1, operation: 'forecast', allow_public_data: true })
  expect(posts[0].parameters).not.toHaveProperty('field')
  expect(posts[0].parameters).not.toHaveProperty('as_of')
  expect(posts[0].parameters.seasons).toEqual([{ planting_date: '2027-05-01' }])
  const plan = page.locator('[data-handoff-plan="plan-a"]')
  await expect(plan.locator('.handoff-metrics').getByText('0 %', { exact: true })).toBeVisible()
  await expect(plan.locator('.handoff-metrics').getByText('Unknown', { exact: true })).toBeVisible()
  await expect(page.getByText('First by the selected criterion:', { exact: false })).toBeVisible()
  await page.screenshot({ path: info.outputPath('comparison-results.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByText('Sources and interpretation', { exact: true }).click()
  await expect(page.getByRole('link', { name: 'https://example.org/synthetic-weather' })).toHaveAttribute('href', 'https://example.org/synthetic-weather')
})

test('response loss retries the identical submission and worker polling never posts again', async ({ page }) => {
  const state = await setup(page, { failFirst: true, queued: true })
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await page.getByRole('button', { name: 'Retry the same request', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  expect(state.posts).toHaveLength(2)
  expect(state.posts[1]).toEqual(state.posts[0])
  expect(state.gets.some(path => path.endsWith('/saved-model-job'))).toBe(true)
})

test('a terminal failure creates a new ID only after explicit Run again', async ({ page }) => {
  const { posts } = await setup(page, { failedJob: true })
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByText('Synthetic provider failure')).toBeVisible()
  await page.getByRole('button', { name: 'Run again', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  expect(posts).toHaveLength(2)
  expect(posts[1].request_id).not.toBe(posts[0].request_id)
  expect(posts[1].parameters).toEqual(posts[0].parameters)
})

for (const suitability of ['no_thermal_growth', 'not_assessed'] as const) test(`${suitability} keeps raw zero output without recommending its rank`, async ({ page }) => {
  const result = resultFixture(false); result.suitability_status = suitability
  await setup(page, { result })
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByRole('complementary', { name: 'Decision brief' }).getByText('Scenario estimates are available; no crop recommendation is established.', { exact: true })).toBeVisible()
  await expect(page.getByText('First by the selected criterion:', { exact: false })).toHaveCount(0)
  await expect(page.locator('[data-handoff-plan="plan-a"] h4')).not.toContainText('Rank')
  await expect(page.locator('[data-handoff-plan="plan-a"] .handoff-metrics').getByText('0 %', { exact: true })).toBeVisible()
})

test('saved active job resumes with GET after reload; status outage is retryable without submission', async ({ page }) => {
  const state = await setup(page, { saved: [jobFixture('north', 'running')], pollFailure: true })
  await page.reload()
  await page.getByText('Saved comparisons for this field boundary', { exact: true }).click()
  await page.getByRole('button', { name: /^Open saved calculation/ }).click()
  await expect(page.getByText('Synthetic status outage')).toBeVisible()
  await page.locator('.handoff-job').getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  expect(state.posts).toHaveLength(0)
  await expect(page.getByLabel('Planting date · 1')).toHaveValue('')
})

test('field scope changes hide another field result and preserve each unfinished form', async ({ page }) => {
  const state = await setup(page)
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toHaveCount(0)
  await expect(page.getByLabel('Planting date · 1')).toHaveValue('')
  await page.getByLabel('Planting date · 1').fill('2028-01-01')
  await page.getByLabel('Field in focus').selectOption('north')
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  await expect(page.getByLabel('Planting date · 1')).toHaveValue('2027-05-01')
  expect(state.posts).toHaveLength(1)
})

test('wrong field response is rejected and unavailable engine has a usable calendar exit', async ({ page }) => {
  await setup(page, { wrongScope: true })
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toHaveCount(0)
  await expect(page.locator('.handoff-workspace [role="alert"]')).toBeVisible()
  await page.getByRole('button', { name: 'Find the next calendar opportunity' }).click()
  await expect(page.getByRole('button', { name: /^Next sowing opportunities/ })).toHaveAttribute('aria-pressed', 'true')
})

test('catalog unavailable remains honest and keyboard/UK controls retain context', async ({ page }) => {
  const catalog = catalogFixture(); catalog.status = 'unavailable'; catalog.reasons = ['Synthetic model service unavailable']
  const state = await setup(page, { catalog })
  await expect(page.getByText('Synthetic model service unavailable')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Calculate comparison', exact: true })).toHaveCount(0)
  const calendar = page.getByRole('button', { name: 'Find the next calendar opportunity' })
  await calendar.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: /^Next sowing opportunities/ })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: /^Compare crop plans/ }).click()
  await page.getByRole('combobox', { name: /Language|Мова/ }).selectOption('uk')
  await expect(page.getByRole('heading', { name: 'Порівняйте, що вирощувати на цьому полі' })).toBeVisible()
  expect(state.posts).toHaveLength(0)
})

test('three plans and cycles carry sourced moisture, zero economics and explicit cached-only inputs', async ({ page }) => {
  const { posts } = await setup(page)
  await page.getByLabel('Crop cycles to compare').selectOption('3')
  await page.getByLabel('Plans to compare').selectOption('3')
  for (let cycle = 1; cycle <= 3; cycle++) {
    await page.getByLabel(`Planting date · ${cycle}`).fill(`${2026 + cycle}-05-01`)
    for (const letter of ['A', 'B', 'C']) {
      const choice = page.locator('.handoff-crop-choice').filter({ has: page.getByLabel(`Crop profile · ${letter}${cycle}`) })
      await choice.getByLabel(`Crop profile · ${letter}${cycle}`).selectOption('maize_grain')
      await choice.getByLabel('Growth-duration calendar').selectOption(catalogFixture().crops.find(row => row.id === 'maize_grain')!.profile.calendars![0].id)
    }
  }
  const firstCycle = page.locator('.handoff-cycles > fieldset').first()
  await firstCycle.getByText('Period and opening moisture', { exact: true }).click()
  await firstCycle.getByLabel('Supply opening soil moisture').check()
  for (const [index, depth] of ['0–7', '7–28', '28–100'].entries()) await firstCycle.getByLabel(`${depth} cm · m³/m³`).fill(index === 0 ? '0' : '0.2')
  await firstCycle.getByLabel('Moisture source').fill('Synthetic observed layer profile')
  const crop = page.locator('.handoff-crop-choice').first()
  await crop.getByText('Yield and financial assumptions', { exact: true }).click()
  await crop.getByLabel('Supply my assumptions').check()
  await crop.getByLabel('Marketable yield, t/ha').fill('0')
  await crop.getByLabel('Farm-gate price / t').fill('120')
  await crop.getByLabel('Currency (three letters)').fill('EUR')
  await crop.getByLabel('Assumption source').fill('Synthetic farmer estimate')
  await page.getByText('Equipment, water access and data sources', { exact: true }).click()
  await page.getByLabel('Use only supplied or cached inputs').check()
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toBeVisible()
  expect(posts[0].allow_public_data).toBe(false)
  expect(posts[0].parameters.seasons).toHaveLength(3)
  expect(posts[0].parameters.plans).toHaveLength(3)
  expect((posts[0].parameters.seasons as unknown[])[0]).toEqual({ planting_date: '2027-05-01', initial_moisture: { theta: [0, 0.2, 0.2], source: 'Synthetic observed layer profile' } })
  expect((posts[0].parameters.plans as Array<{ crops: Array<{ economics: unknown }> }>)[0].crops[0].economics).toEqual({ marketable_yield_t_ha: 0, farmgate_price_per_t: 120, currency: 'EUR', source: 'Synthetic farmer estimate' })
})

test('late submission reply cannot attach to another field and interrupted original remains retryable', async ({ page }) => {
  await setup(page)
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let body: HandoffSubmission | undefined
  await page.route('**/api/rotations/handoff/fields/north/jobs', async route => {
    if (route.request().method() !== 'POST') return route.fallback()
    body = route.request().postDataJSON()
    await pending
    await route.fulfill({ json: jobFixture() }).catch(() => {}) // browser abort is expected after switching field
  })
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect.poll(() => body?.request_id).toBeTruthy()
  await page.getByLabel('Field in focus').selectOption('south')
  release()
  await expect(page.getByRole('heading', { name: 'Your calculated comparison' })).toHaveCount(0)
  await page.getByLabel('Field in focus').selectOption('north')
  await expect(page.getByRole('button', { name: 'Retry the same request', exact: true })).toBeVisible()
  await expect(page.getByText('The submission response was interrupted.', { exact: false })).toBeVisible()
})

for (const uk of [false, true]) test(`failed comparison keeps a keyboard calendar exit and draft (${uk ? 'UK' : 'EN'}, 320px)`, async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 })
  const state = await setup(page, { failedJob: true })
  const guidance = uk ? 'Порівняння не отримано. Помилка розрахунку не означає, що ці культури непридатні.' : 'No comparison was produced. A calculation failure does not establish that these crops are unsuitable.'
  await expect(page.getByText(guidance, { exact: true })).toHaveCount(0)
  await fill(page)
  await page.getByRole('button', { name: 'Calculate comparison', exact: true }).click()
  await expect(page.getByText('Synthetic provider failure')).toBeVisible()
  if (uk) await page.getByRole('combobox', { name: /Language|Мова/ }).selectOption('uk')
  await expect(page.getByText(guidance, { exact: true })).toBeVisible()
  await expect(page.locator('.handoff-outcome')).toHaveCount(0)
  const name = uk ? 'Знайти найближчу календарну можливість' : 'Find the next calendar opportunity'
  const calendar = page.getByRole('button', { name, exact: true })
  await expect(calendar).toHaveCount(1)
  await expect(page.locator('.handoff-job').getByRole('button', { name, exact: true })).toBeVisible()
  const bounds = await calendar.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.height).toBeGreaterThanOrEqual(44)
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await calendar.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: uk ? /^Найближча сівба/ : /^Next sowing opportunities/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel(uk ? 'Поле у фокусі' : 'Field in focus')).toHaveValue('north')
  expect(state.calendarFields).toContain('north')
  await page.getByRole('button', { name: uk ? /^Порівняти плани культур/ : /^Compare crop plans/ }).click()
  await expect(page.getByLabel(uk ? 'Дата сівби · 1' : 'Planting date · 1')).toHaveValue('2027-05-01')
  await expect(page.getByLabel(uk ? 'Профіль культури · A1' : 'Crop profile · A1')).toHaveValue('maize_grain')
  await expect(page.getByText(guidance, { exact: true })).toBeVisible()
  expect(state.posts).toHaveLength(1)
})

test('reopened terminal failure has a calendar exit without restoring or resubmitting saved inputs', async ({ page }) => {
  const failed = jobFixture('north', 'failed'); failed.attempts = 3; failed.error = null
  const catalog = catalogFixture(); catalog.status = 'unavailable'; catalog.reasons = ['Synthetic model unavailable']
  const state = await setup(page, { saved: [failed], catalog })
  await page.getByText('Saved comparisons for this field boundary', { exact: true }).click()
  await page.getByRole('button', { name: /^Open saved calculation/ }).click()
  await expect(page.getByText('No comparison was produced. A calculation failure does not establish that these crops are unsuitable.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run again', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Calculate comparison', exact: true })).toHaveCount(0)
  await page.locator('.handoff-job').getByRole('button', { name: 'Find the next calendar opportunity' }).click()
  await expect(page.getByRole('button', { name: /^Next sowing opportunities/ })).toHaveAttribute('aria-pressed', 'true')
  expect(state.posts).toHaveLength(0)
})
