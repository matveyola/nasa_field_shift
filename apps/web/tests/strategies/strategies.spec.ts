import { test, expect, type Page } from '@playwright/test'
import { opportunities } from './opportunityFixtures'
import { fixture } from '../planning/fixtures'
import type { FarmPlanningRequest } from '../../src/api/planningV1.generated'
import type { PlanningDraft, PlanningRequest, PlanningField } from '../../src/api/rotationPlanning'

async function setup(page: Page, failFirst = false, openAllocation = true) {
  const fields: PlanningField[] = [{ id: 'north', name: 'North field', area_ha: 20 }, { id: 'south', name: 'South field', area_ha: 35.6 }]
    .map(field => ({ ...field, boundary_revision: 1, crop: 'Wheat', crop_season: 2026, history: [{ field_id: field.id, season: 2026, crop: 'Wheat' }] }))
  const requests: PlanningRequest[] = []
  let applyCount = 0
  let saved: PlanningDraft | null = null
  const contextYears: number[] = []
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname.endsWith('/planting-opportunities')) return route.fulfill({ json: opportunities(url.pathname.split('/')[3], url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count'))) })
    if (url.pathname === '/api/planning-workspace') return route.fulfill({ json: {
      farm_id: 'synthetic-test-farm', profiles: [], missing_profiles: [{ field_id: 'north', name: 'North field', code: 'not_found' }],
      request: null, crop_options: [], model_card: null, configuration: null, status: 'unavailable', reason: 'Trusted weather and regional rules are not configured',
    } })
    if (url.pathname.endsWith('/context')) {
      const year = Number(url.searchParams.get('start_season')); contextYears.push(year)
      return route.fulfill({ json: { start_season: year, season_count: Number(url.searchParams.get('season_count') ?? 3), min_season_count: 1, max_season_count: 6, fields, crops: ['Wheat', 'Maize', 'Soy', 'Barley', 'Sunflower', 'Rapeseed', 'Peas'].map(crop => ({ crop, family: 'test' })) } })
    }
    if (url.pathname.endsWith('/apply')) {
      applyCount += 1
      saved!.applied_at = '2026-11-15T00:00:00Z'
      return route.fulfill({ json: saved })
    }
    if (url.pathname.endsWith('/drafts') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as PlanningRequest; requests.push(body)
      if (failFirst && requests.length === 1) return route.fulfill({ status: 503, json: { detail: 'Temporary allocation outage' } })
      saved = { id: 'test-draft', name: body.name, start_season: body.start_season, season_count: body.season_count ?? 3, created_at: '2026-11-15T00:00:00Z', applied_at: null, request: body, fields,
        result: { status: 'feasible', assignments: fields.flatMap(field => Array.from({ length: body.season_count ?? 3 }, (_, index) => index).map(index => ({ field_id: field.id, season: body.start_season + index, crop: index === 1 ? 'Maize' : 'Soy', area_ha: field.area_ha, locked: false, reasons: ['Synthetic test: supplied crop preference and return break'] }))),
          areas: [], diagnostics: [], warnings: [], explored_nodes: 100, node_limit: 100, generation_nodes: 20, generation_limit: 200000, generation_complete: true, time_limit_seconds: 2, objective: 0, objective_unit: 'preference_ha', method: 'synthetic-ui-fixture' } }
      return route.fulfill({ json: saved })
    }
    if (url.pathname.endsWith('/drafts')) return route.fulfill({ json: saved ? [saved] : [] })
    if (url.pathname.endsWith('/test-draft')) return route.fulfill({ json: saved })
    return route.fulfill({ status: 404, json: { detail: 'Unexpected test route' } })
  })
  await page.goto('/tests/strategies/index.html')
  if (openAllocation) await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  else await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  return { requests, contextYears, applied: () => applyCount }
}

test('allocation is usable without model configuration; decisions, recovery and confirmation remain real interactions', async ({ page }, testInfo) => {
  const state = await setup(page, true)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('What to sow next?')
  await expect(page.getByRole('button', { name: 'Generate and save draft' })).toBeVisible()
  expect(state.contextYears).toEqual([2027])
  await page.screenshot({ path: testInfo.outputPath('allocation-start.png'), fullPage: true })
  await expect(page.getByText('Return break, years', { exact: true }).first()).not.toBeVisible()
  await page.getByText('Fields and plan name', { exact: true }).click()
  await page.getByLabel('Plan name', { exact: true }).fill('Procurement 2027')
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByText('Temporary allocation outage', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('allocation-result.png'), fullPage: true })
  expect(state.requests).toHaveLength(2)
  expect(state.requests[1].season_count).toBe(1)
  expect(state.requests[1].crops).toHaveLength(7)
  expect(state.requests[1].require_two_crops).toBe(false)
  expect(state.requests[0].client_id).toEqual(state.requests[1].client_id)
  await expect(page.locator('.strategy-decisions')).toContainText('North field')
  await expect(page.locator('.strategy-decisions')).toContainText('Synthetic test: supplied crop preference')
  await expect(page.locator('.strategy-decisions')).toContainText('Yield and margin have not been calculated')
  await page.getByRole('button', { name: 'Scenario comparison', exact: false }).click()
  await expect(page.getByRole('heading', { name: 'Forecasts are not ready to compare' })).toBeVisible()
  await expect(page.getByText('Crop allocation remains available separately.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByLabel('Plan name', { exact: true })).toHaveValue('Procurement 2027')
  await page.getByRole('button', { name: 'Inspect 2028 externally' }).click()
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  expect(state.contextYears).toEqual([2027])
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(page.locator('.strategy-decision').first()).toContainText('South field')
  await page.getByRole('button', { name: 'Discuss with assistant' }).click()
  await expect(page.locator('output')).toHaveText('agent:south:2028')
  await page.getByRole('button', { name: 'Apply to selected fields' }).click()
  expect(state.applied()).toBe(0)
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(state.applied()).toBe(0)
  await page.getByRole('button', { name: 'Apply to selected fields' }).click()
  await page.getByRole('button', { name: 'Confirm crop plan' }).click()
  await expect(page.getByText('Crop plans saved for every selected field.')).toBeVisible()
  expect(state.applied()).toBe(1)
  await page.getByText('Saved allocations', { exact: true }).click()
  await page.getByLabel('Saved plans', { exact: true }).selectOption('test-draft')
  await expect(page.getByLabel('Plan name', { exact: true })).toHaveValue('Procurement 2027')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('Ukrainian guidance, detailed constraints, profile readiness and explicit period changes', async ({ page }) => {
  const state = await setup(page)
  await page.getByLabel('Language').selectOption('uk')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Що сіяти наступним?')
  await page.getByText('Культури, обмеження та зафіксовані рішення', { exact: true }).click()
  await expect(page.getByText('Перерва перед поверненням, років', { exact: true }).first()).toBeVisible()
  await page.getByRole('combobox', { name: 'Перший рік урожаю', exact: true }).selectOption('2029')
  await expect.poll(() => state.contextYears.at(-1)).toBe(2029)
  await page.getByRole('button', { name: 'Порівняння сценаріїв', exact: false }).click()
  await page.getByText('Паспорти полів та перевірка вхідних даних', { exact: true }).click()
  await expect(page.getByRole('button', { name: 'Створити паспорт поля' })).toBeVisible()
  await expect(page.getByText('Розподіл культур доступний окремо.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Переглянути водні умови' }).click()
  await expect(page.locator('output')).toHaveText('analytics:water')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('forecast HTTP failure does not remove allocation controls', async ({ page }) => {
  await setup(page)
  await page.route('**/api/planning-workspace', route => route.fulfill({ status: 503, json: { detail: 'Forecast service down' } }))
  await page.reload()
  await page.getByRole('button', { name: 'Scenario comparison', exact: false }).click()
  await expect(page.getByRole('heading', { name: 'Scenario calculation unavailable' })).toBeVisible()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByRole('button', { name: 'Generate and save draft' })).toBeVisible()
})

test('empty field catalog cannot produce a draft or invented decisions', async ({ page }) => {
  await setup(page)
  await page.route('**/api/rotations/planning/context?*', route => route.fulfill({ json: { start_season: 2027, season_count: 1, min_season_count: 1, max_season_count: 6, fields: [], crops: [] } }))
  await page.reload()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByText('No registered fields are available for allocation.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate and save draft' })).toBeDisabled()
  await expect(page.locator('.strategy-decision')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Apply to selected fields' })).toHaveCount(0)
})

test('configured scenarios use field names and submit the chosen modeled horizon', async ({ page }) => {
  await setup(page)
  const data = fixture()
  data.run.request.weather.stale_after = '2099-01-01T00:00:00Z'
  const requests: FarmPlanningRequest[] = []
  await page.route('**/api/planning-workspace', route => route.fulfill({ json: {
    farm_id: data.run.request.farm_id, profiles: data.run.request.fields, missing_profiles: [], request: data.run.request,
    crop_options: data.crops, model_card: null, configuration: 'synthetic-test-only', status: 'ready', reason: '',
  } }))
  await page.route('**/api/planning-workspace/farms/**/evidence', route => route.fulfill({ json: { candidates: [], evaluations: [] } }))
  await page.route('**/api/planning-v1/farms/**/runs', route => {
    const request = route.request().postDataJSON() as FarmPlanningRequest
    requests.push(request)
    return route.fulfill({ json: { ...data.run, request, status: 'queued', comparison: null } })
  })
  await page.goto('/tests/strategies/index.html?configured=1')
  await page.getByRole('button', { name: 'Scenario comparison', exact: false }).click()
  await expect(page.getByText('Synthetic demonstration — not measured accuracy', { exact: true })).toBeVisible()
  await expect(page.locator('.planning-workspace').getByRole('combobox', { name: 'Field', exact: true }).locator('option').first()).toHaveText('North field')
  await page.getByRole('combobox', { name: 'Planning years', exact: true }).selectOption('2')
  await expect(page.getByRole('combobox', { name: 'Goal', exact: true }).locator('option')).toHaveText(['Reference scenario margin', 'Worst tested margin', 'Water demand'])
  await expect(page.getByLabel('Budget', { exact: true }).first()).not.toBeVisible()
  await page.getByRole('button', { name: 'Save and calculate', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: /^Queued$/ })).toBeVisible()
  expect(requests).toHaveLength(1)
  expect(requests[0].season_count).toBe(2)
  await expect(page.getByRole('button', { name: 'Apply selected plan' })).toBeDisabled()
})

test('allocation horizon changes invalidate old drafts and preserve explicit constraints', async ({ page }) => {
  const state = await setup(page)
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  await page.getByLabel('Allocation planning years').selectOption('6')
  await expect(page.getByRole('button', { name: 'Apply to selected fields' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  expect(state.requests.at(-1)?.season_count).toBe(6)
  expect(state.requests.at(-1)?.crops).toHaveLength(7)
  await expect(page.locator('.planner-field-results article').first().locator('section')).toHaveCount(6)
  await page.getByText('Crops, constraints and commitments', { exact: true }).click()
  await page.getByLabel('At least two different crops on every field', { exact: true }).check()
  await page.getByLabel('Allocation planning years').selectOption('1')
  await expect(page.getByText('The two-crop requirement conflicts with one year.', { exact: false })).toBeVisible()
  const before = state.requests.length
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByText('For one year, turn off the requirement', { exact: false })).toBeVisible()
  expect(state.requests).toHaveLength(before)
  await page.getByLabel('At least two different crops on every field', { exact: true }).uncheck()
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect.poll(() => state.requests.length).toBe(before + 1)
  expect(state.requests.at(-1)?.season_count).toBe(1)
})

test('saved durations restore without losing the plan, and year bounds are explicit', async ({ page }) => {
  const state = await setup(page)
  await page.getByLabel('Allocation planning years').selectOption('3')
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  await page.getByLabel('Allocation planning years').selectOption('1')
  await page.getByText('Saved allocations', { exact: true }).click()
  await page.getByLabel('Saved plans', { exact: true }).selectOption('test-draft')
  await expect(page.getByLabel('Allocation planning years')).toHaveValue('3')
  await expect(page.getByRole('heading', { name: 'Choices for 2027' })).toBeVisible()
  await expect(page.locator('.planner-field-results article').first().locator('section')).toHaveCount(3)
  await page.getByLabel('Allocation planning years').selectOption('1')
  await page.getByRole('combobox', { name: 'First harvest year', exact: true }).selectOption('2100')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Start the new period', exact: true }).click()
  await expect.poll(() => state.contextYears.at(-1)).toBe(2100)
  await expect(page.getByLabel('Allocation planning years').locator('option[value="6"]')).toBeDisabled()
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect.poll(() => state.requests.at(-1)?.start_season).toBe(2100)
  expect(state.requests.at(-1)?.season_count).toBe(1)
})

test('sowing dates come from saved assignments and remain distinct from weather optimization', async ({ page }) => {
  await setup(page)
  const data = fixture()
  data.run.request.weather.stale_after = '2099-01-01T00:00:00Z'
  data.run.comparison!.alternatives[0].assignments[0].sowing_window = { start: '2026-09-20', end: '2026-10-10' }
  await page.route('**/api/planning-workspace', route => route.fulfill({ json: {
    farm_id: data.run.request.farm_id, profiles: data.run.request.fields, missing_profiles: [], request: data.run.request,
    crop_options: data.crops, model_card: null, configuration: 'synthetic-test-only', status: 'ready', reason: '',
  } }))
  await page.route('**/api/planning-workspace/farms/**/evidence', route => route.fulfill({ json: data.evidence }))
  await page.route('**/api/planning-v1/farms/**/runs', route => route.fulfill({ json: { ...data.run, request: route.request().postDataJSON() } }))
  await page.goto('/tests/strategies/index.html?configured=1')
  await page.getByRole('button', { name: 'Scenario comparison', exact: false }).click()
  await page.getByRole('button', { name: 'Save and calculate', exact: true }).click()
  const card = page.locator('.pl-cards > article').first()
  await card.getByText('Sowing window', { exact: true }).click()
  await expect(card.getByText('2026-09-20 – 2026-10-10', { exact: true })).toBeVisible()
  await expect(card.getByText('Calendar constraint, not a weather-optimal date forecast', { exact: true })).toBeVisible()
  await expect(card.getByText('Weather-based sowing timing: Not assessed', { exact: true })).toBeVisible()
  await expect(card.getByText('Saved assignment', { exact: false })).toContainText(data.run.request.rulebook_version)
})

test('calendar entry retains separate reviewed, typical, passed and unsupported evidence', async ({ page }, testInfo) => {
  await setup(page, false, false)
  await page.getByLabel('Planning date', { exact: true }).fill('2027-06-15')
  await expect(page.getByRole('heading', { name: 'What could you consider sowing next?' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Reviewed windows open on the planning date', exact: false })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Current reference month', exact: false })).toBeVisible()
  const winter = page.locator('[data-opportunity-id="winter-wheat"]')
  await expect(winter).toContainText('September 2027')
  await expect(winter).not.toContainText('1 September')
  await expect(winter).toContainText('July 2028')
  await expect(winter).toContainText('regional reference, not a range of admissible dates')
  await winter.getByText('Why this entry, and where is it from?', { exact: true }).click()
  await expect(winter).toContainText('synthetic-calendar-v1')
  await expect(winter).toContainText('Sowing weather: not assessed. Profit: not assessed.')
  await expect(winter.getByRole('link', { name: 'Calendar source' })).toHaveAttribute('href', 'https://example.org/test-calendar')
  await expect(page.locator('[data-opportunity-id="reviewed"]')).toContainText('10 June 2027 – 20 June 2027')
  await expect(page.locator('[data-opportunity-id="rice"]').getByRole('button')).toHaveCount(0)
  await page.getByText('Passed windows and reference months (1)', { exact: true }).click()
  await expect(page.locator('[data-opportunity-id="passed"]').getByRole('button')).toHaveCount(0)
  await page.getByText('Missing calendar data (1)', { exact: true }).click()
  await expect(page.locator('[data-opportunity-id="peas"]')).toContainText('Calendar unknown')
  await expect(page.locator('[data-opportunity-id="peas"]').getByRole('button')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('planting-opportunities.png'), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('maturity year prefills allocation, while an edited draft requires explicit replacement', async ({ page }) => {
  const state = await setup(page)
  await page.getByText('Fields and plan name', { exact: true }).click()
  await page.getByLabel('Plan name', { exact: true }).fill('Keep my procurement draft')
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Keep current draft', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByLabel('Plan name', { exact: true })).toHaveValue('Keep my procurement draft')
  expect(state.contextYears).toEqual([2027])
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button').click()
  await page.getByRole('button', { name: 'Start the new period', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'First harvest year', exact: true })).toHaveValue('2028')
  await expect.poll(() => state.contextYears.at(-1)).toBe(2028)
  await expect(page.getByText('Calendar idea:', { exact: false })).toContainText('No crop is locked')
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect.poll(() => state.requests.length).toBe(1)
  expect(state.requests[0].start_season).toBe(2028)
  expect(state.requests[0].locked).toEqual([])
  expect(state.requests[0].crops).toHaveLength(7)
})

test('same-year calendar review preserves the draft and field changes preserve allocation edits', async ({ page }) => {
  await setup(page)
  await page.getByText('Fields and plan name', { exact: true }).click()
  await page.getByLabel('Plan name', { exact: true }).fill('Same year draft')
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await page.locator('[data-opportunity-id="reviewed"]').getByRole('button').click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByLabel('Plan name', { exact: true })).toHaveValue('Same year draft')
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(page.getByText('Calendar idea:', { exact: false })).toHaveCount(0)
  await expect(page.getByLabel('Plan name', { exact: true })).toHaveValue('Same year draft')
})

test('calendar date and rolling horizon use typed query parameters and reject cross-field responses', async ({ page }) => {
  await setup(page, false, false)
  const queries: URL[] = []
  await page.route('**/planting-opportunities?*', route => {
    const url = new URL(route.request().url()); queries.push(url)
    return route.fulfill({ json: opportunities('wrong-field', url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count'))) })
  })
  await page.getByLabel('Planning date', { exact: true }).fill('2028-02-29')
  await page.getByLabel('Calendar lookahead, years').selectOption('6')
  await expect(page.getByText('Calendar does not match', { exact: false })).toBeVisible()
  expect(queries.at(-1)?.searchParams.get('planning_date')).toBe('2028-02-29')
  expect(queries.at(-1)?.searchParams.get('year_count')).toBe('6')
  await expect(page.locator('.opportunity-card')).toHaveCount(0)
  await page.getByLabel('Planning date', { exact: true }).fill('')
  await expect(page.getByText('Enter a valid planning date from 1900 with a lookahead ending by 2100.', { exact: true })).toBeVisible()
  await expect(page.locator('.opportunity-card')).toHaveCount(0)
})

test('calendar failures retry and empty results do not imply impossibility or block allocation', async ({ page }) => {
  await setup(page, false, false)
  let failure = true
  await page.route('**/planting-opportunities?*', route => {
    const url = new URL(route.request().url())
    return failure ? route.fulfill({ status: 503, json: { detail: 'Calendar provider unavailable' } }) : route.fulfill({ json: { ...opportunities('north', url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count'))), items: [] } })
  })
  await page.getByLabel('Calendar lookahead, years').selectOption('2')
  await expect(page.getByText('Calendar provider unavailable', { exact: true })).toBeVisible()
  failure = false
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.getByText('This does not establish that sowing is impossible.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByRole('button', { name: 'Generate and save draft' })).toBeVisible()
})

test('Ukrainian calendar guidance and input readiness remain available without a model', async ({ page }) => {
  await setup(page, false, false)
  await page.getByLabel('Language').selectOption('uk')
  await expect(page.getByRole('heading', { name: 'Що можна розглянути для наступної сівби?' })).toBeVisible()
  await expect(page.getByLabel('Дата планування', { exact: true })).toBeVisible()
  await expect(page.locator('[data-opportunity-id="winter-wheat"]')).toContainText('не інтервал дозволених дат')
  await page.getByRole('button', { name: 'Переглянути дані для оцінки', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Прогноз ще не готовий до порівняння' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Створити паспорт поля' })).toBeVisible()
})

test('late calendar responses cannot replace the newly selected field calendar', async ({ page }) => {
  await setup(page, false, false)
  let release!: () => void
  const wait = new Promise<void>(resolve => { release = resolve })
  let held = false
  await page.route('**/planting-opportunities?*', async route => {
    const url = new URL(route.request().url())
    const field = url.pathname.split('/')[3]
    if (field === 'north') { held = true; await wait }
    const data = opportunities(field, url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count')))
    data.items = [{ ...data.items[0], id: field + '-only', cycle_id: field + '-cycle' }]
    await route.fulfill({ json: data })
  })
  await page.getByLabel('Calendar lookahead, years').selectOption('2')
  await expect.poll(() => held).toBe(true)
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(page.locator('[data-opportunity-id="south-only"]')).toBeVisible()
  release()
  await expect(page.locator('[data-opportunity-id="north-only"]')).toHaveCount(0)
  await expect(page.locator('[data-opportunity-id="south-only"]')).toBeVisible()
})

test('an allocation mutation in flight prevents calendar replacement and remains recoverable', async ({ page }) => {
  await setup(page)
  let release!: () => void
  const wait = new Promise<void>(resolve => { release = resolve })
  let held = false
  await page.route('**/api/rotations/planning/drafts', async route => {
    if (route.request().method() !== 'POST') return route.fallback()
    held = true; await wait
    return route.fulfill({ status: 503, json: { detail: 'Synthetic delayed failure' } })
  })
  await page.getByRole('button', { name: 'Generate and save draft' }).click()
  await expect.poll(() => held).toBe(true)
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await expect(page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button')).toBeDisabled()
  release()
  await expect(page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button')).toBeEnabled()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByText('Synthetic delayed failure', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Generate and save draft' })).toBeEnabled()
})

test('a calendar handoff never silently shortens an allocation at the year limit', async ({ page }) => {
  await setup(page)
  await page.getByLabel('Allocation planning years').selectOption('6')
  await page.route('**/planting-opportunities?*', route => {
    const url = new URL(route.request().url())
    const data = opportunities('north', url.searchParams.get('planning_date')!, Number(url.searchParams.get('year_count')))
    data.items = [{ ...data.items[0], harvest_year: 2100 }]
    return route.fulfill({ json: data })
  })
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await page.getByLabel('Planning date', { exact: true }).fill('2099-06-15')
  await page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button').click()
  await expect(page.getByRole('dialog')).toContainText('Confirming changes the allocation to one harvest year: 2100')
  await page.getByRole('button', { name: 'Keep current draft', exact: true }).click()
  await page.getByRole('button', { name: 'Crop allocation', exact: false }).click()
  await expect(page.getByLabel('Allocation planning years')).toHaveValue('6')
  await page.getByRole('button', { name: 'Next sowing opportunities', exact: false }).click()
  await page.locator('[data-opportunity-id="winter-wheat"]').getByRole('button').click()
  await page.getByRole('button', { name: 'Start the new period', exact: true }).click()
  await expect(page.getByLabel('Allocation planning years')).toHaveValue('1')
  await expect(page.getByRole('combobox', { name: 'First harvest year', exact: true })).toHaveValue('2100')
})
