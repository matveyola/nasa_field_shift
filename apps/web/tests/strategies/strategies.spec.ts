import { createRequire } from 'node:module'
import type { Page } from '../../../../services/agent/frontend/node_modules/@playwright/test'
const { test, expect } = createRequire(import.meta.url)('../../../../services/agent/frontend/node_modules/@playwright/test') as typeof import('../../../../services/agent/frontend/node_modules/@playwright/test')
import { fixture } from '../planning/fixtures'
import type { FarmPlanningRequest } from '../../src/api/planningV1.generated'
import type { PlanningDraft, PlanningRequest, PlanningField } from '../../src/api/rotationPlanning'

async function setup(page: Page, failFirst = false) {
  const fields: PlanningField[] = [{ id: 'north', name: 'North field', area_ha: 20 }, { id: 'south', name: 'South field', area_ha: 35.6 }]
    .map(field => ({ ...field, boundary_revision: 1, crop: 'Wheat', crop_season: 2026, history: [{ field_id: field.id, season: 2026, crop: 'Wheat' }] }))
  const requests: PlanningRequest[] = []
  let applyCount = 0
  let saved: PlanningDraft | null = null
  const contextYears: number[] = []
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
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
