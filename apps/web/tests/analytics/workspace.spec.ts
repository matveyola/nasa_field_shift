import { test, expect } from '@playwright/test'
import { example } from '../../../../contracts/planning-v1/examples/roundtrip.generated'

const observations = ['2026-09-15', '2026-10-15'].map(date => ({ date, scene_id: `scene-${date}`, mean_ndvi: .6, p10_ndvi: .4, p90_ndvi: .8, valid_percent: 85, pixel_count: 100, catalog_url: 'https://example.test/scene', mean_msavi: .5 }))
test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date('2026-11-15T12:00:00Z') })
  await page.route('http://127.0.0.1:18103/api/**', async route => {
    const url = new URL(route.request().url())
    let body: unknown = null
    if (url.pathname.endsWith('/planning-workspace')) {
      const profile = structuredClone(example.field_profile)
      Object.assign(profile, { field_id: 'one', geometry_revision: 0, soil: [], history: [
        { season: 2025, crop: { crop_id: 'Maize', cultivar_id: null, profile_version: null }, status: 'reported', source_ids: [], sowing_date: null, harvest_date: null },
      ] })
      body = { farm_id: profile.farm_id, profiles: [profile], missing_profiles: [], request: null, crop_options: [], model_card: null, configuration: null, status: 'unknown', reason: 'test only' }
    } else if (url.pathname.endsWith('/vegetation')) {
      const fieldId = url.pathname.split('/')[3]
      const season = Number(url.searchParams.get('season'))
      body = { field_id: fieldId, season, boundary_revision: 0, geometry_fingerprint: 'test', generated_at: '2026-11-15T00:00:00Z', status: 'partial', sampling: 'monthly_clear_scene', resolution_m: 10,
        observations: fieldId === 'one' && season === 2026 ? observations : [], heterogeneity: null, months: [], source_url: 'https://example.test', limitations: [] }
    } else if (url.pathname.endsWith('/jobs')) body = []
    else if (url.pathname === '/api/rotations') body = { plans: [], main_plan_id: null, demo: false }
    else if (url.pathname === '/api/rotations/yield') body = { field_id: url.searchParams.get('field_id'), season: Number(url.searchParams.get('season')), status: 'unavailable', plans: [] }
    await route.fulfill({ json: body })
  })
})

test('all five sections retain context and fit the viewport', async ({ page }) => {
  await page.goto('/tests/analytics/index.html')
  await expect(page.getByRole('heading', { level: 1, name: 'Analytics' })).toBeVisible()
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('two')
  await page.getByRole('combobox', { name: 'Season', exact: true }).selectOption('2025')
  for (const name of ['Water', 'Vegetation', 'Rotations', 'Soil', 'Overview']) {
    await page.getByRole('navigation', { name: 'Analytics' }).getByRole('button', { name, exact: true }).click()
    await expect(page.getByRole('combobox', { name: 'Field', exact: true })).toHaveValue('two')
    await expect(page.getByRole('combobox', { name: 'Season', exact: true })).toHaveValue('2025')
    await expect(page.getByRole('combobox', { name: 'Scenario', exact: true })).toHaveValue('dry_year')
    expect(await page.locator('h1').count()).toBe(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
  await page.screenshot({ path: test.info().outputPath('analytics-overview.png'), fullPage: true })
  await page.getByRole('button', { name: 'Review strategies', exact: true }).click()
  await expect(page.getByTestId('action')).toHaveText('strategies:two:2025:dry_year')
  await page.getByRole('button', { name: 'Ask the assistant', exact: true }).click()
  await expect(page.getByTestId('action')).toHaveText('agent:two:2025:dry_year:')
})

test('map actions carry selected observation and explicit index without stale state', async ({ page }) => {
  await page.goto('/tests/analytics/index.html?section=vegetation')
  await expect(page.getByText('Older than 30 days — inspect before acting on this observation.')).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('analytics-vegetation.png'), fullPage: true })
  const earlier = page.locator('.ndvi-observation-dates button').first()
  await earlier.click()
  await expect(page.getByTestId('action')).toHaveText('')
  await page.getByRole('button', { name: 'Show MSAVI on map', exact: true }).click()
  await expect(page.getByTestId('action')).toContainText('"observationDate":"2026-09-15"')
  await expect(page.getByTestId('action')).toContainText('"index":"msavi"')
  await page.getByRole('combobox', { name: 'Compare with another observation' }).selectOption('2026-10-15')
  await page.getByRole('button', { name: 'Compare dates on the map' }).click()
  await expect(page.getByTestId('action')).toContainText('"comparisonDate":"2026-10-15"')
  await expect(page.getByTestId('action')).toContainText('"comparison":true')
})

test('rotation history is separate from future planning and missing profiles stay actionable', async ({ page }) => {
  await page.goto('/tests/analytics/index.html?section=rotation')
  await expect(page.locator('.analytics-history-timeline')).toContainText('2025')
  await expect(page.locator('.analytics-history-timeline')).toContainText('Reported')
  await page.getByText('Saved sequence diagnostics', { exact: true }).click()
  await expect(page.getByText('No rotation plans are available for this field.')).toBeVisible()
  await expect(page.getByText('Create farm plan', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Set as main plan' })).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('two')
  await expect(page.getByText('No saved profile for this field. Add its soil and crop history in Strategies.')).toBeVisible()
  await expect(page.locator('.analytics-history-timeline')).toHaveCount(0)
})

test('Ukrainian headings and empty-state actions remain usable', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('field-shift-language', 'uk'))
  await page.goto('/tests/analytics/index.html?section=soil')
  await expect(page.getByRole('heading', { level: 1, name: 'Аналітика' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Переглянути стратегії' })).toBeVisible()
  await expect(page.getByText('Ґрунтових даних недостатньо для оцінки доступної води.')).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Сезон', exact: true })).toHaveValue('2026')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})


test('historical water controls reset with season and keep scenario and saved-job context', async ({ page }) => {
  let submitted: Record<string, unknown> | undefined
  const result = { field_id: 'two', season: 2025, scenario: 'dry_year', demo: false, source: 'historical', threshold_mm: 50,
    flowering_start: null, flowering_end: null, first_stress_date: null, min_reserve_mm: 80, stress_days: 0,
    days: [{ date: '2025-08-01', reserve_mm: 80, opening_mm: 80, rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0 }] }
  const job = { id: 'saved-run', kind: 'simulation', field_id: 'two', boundary_revision: 0, analysis: 'water_balance', status: 'succeeded',
    attempts: 1, result, error: null, created_at: '2026-11-15T10:00:00Z', updated_at: '2026-11-15T10:00:00Z' }
  await page.route('http://127.0.0.1:18103/api/jobs**', async route => {
    if (route.request().method() === 'POST') { submitted = route.request().postDataJSON(); await route.fulfill({ json: job }) }
    else await route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/saved-run') ? job : [] })
  })
  await page.goto('/tests/analytics/index.html?section=water')
  await page.getByRole('button', { name: 'Scenarios', exact: true }).click()
  await expect(page.getByLabel('From', { exact: true })).toHaveValue('2026-08-01')
  await page.getByRole('combobox', { name: 'Field', exact: true }).selectOption('two')
  await page.getByRole('combobox', { name: 'Season', exact: true }).selectOption('2025')
  await page.getByRole('button', { name: 'Scenarios', exact: true }).click()
  await expect(page.getByLabel('From', { exact: true })).toHaveValue('2025-08-01')
  await expect(page.getByRole('combobox', { name: 'Scenario', exact: true })).toHaveCount(1)
  await page.getByRole('button', { name: 'Run simulation', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Root-zone water reserve', exact: true })).toBeVisible()
  expect(submitted).toMatchObject({ field_id: 'two', inputs: { season: 2025, scenario: 'dry_year', source: 'historical', start_date: '2025-08-01' } })
  await page.getByRole('button', { name: 'Ask agent', exact: true }).click()
  await expect(page.getByTestId('action')).toHaveText('agent:two:2025:dry_year:saved-run')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('six-season reference sequences do not become measured stress evidence', async ({ page }) => {
  await page.route('http://127.0.0.1:18103/api/rotations?**', route => route.fulfill({ json: {
    demo: true, main_plan_id: 'reference', plans: [{ id: 'reference', name: 'Reference only', stress_days: 999,
      longest_episode: 999, sensitive_days: 999, crops: Array.from({ length: 6 }, (_, i) => ({ year: 2026 + i, crop: i % 2 ? 'Soy' : 'Maize' })) }],
  } }))
  await page.goto('/tests/analytics/index.html?section=rotation')
  await page.getByText('Saved sequence diagnostics', { exact: true }).click()
  await expect(page.getByText('Reference sequences. Demo stress days are not measurements of this field; run a historical comparison.')).toBeVisible()
  await expect(page.locator('.rotation-crop-year')).toHaveCount(6)
  await expect(page.locator('.rotation-crop-year').last()).toHaveText('2031')
  await expect(page.locator('.farm-planner')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Edit crop sequence' })).toHaveCount(0)
  await expect(page.getByText('999', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Run historical comparison' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
