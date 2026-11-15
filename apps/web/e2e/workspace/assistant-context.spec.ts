/** Synthetic transport fixtures verify App wiring, not model accuracy or agent execution. */
import { test, expect, type Page } from '@playwright/test'
const run = '0b6f4a39-58c4-4d39-9f4d-3c8c25c2f6a1'
const geometry = { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] }
const field = { id: 'F-03', name: 'Synthetic context test', crop: 'Maize', area_ha: 1, geometry, boundary_revision: 2, planting: null }
const water = { field_id: field.id, season: 2026, scenario: 'less_rain', demo: false, source: 'historical',
  threshold_mm: 40, flowering_start: null, flowering_end: null, first_stress_date: null,
  min_reserve_mm: 90, stress_days: 0, days: [{ date: '2026-08-01', opening_mm: 100, reserve_mm: 90,
    rain_mm: 0, irrigation_mm: 0, water_use_mm: 10, drainage_mm: 0 }] }
const job = { id: run, kind: 'simulation', analysis: 'water_balance', field_id: field.id, boundary_revision: 2,
  status: 'succeeded', result: water, created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z', error: null }
async function fixture(page: Page) {
  const contexts: string[] = []
  await page.addInitScript(() => sessionStorage.setItem('field-shift-desktop', '1'))
  await page.route('http://127.0.0.1:18112/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let json: unknown
    if (path === '/api/auth/session') json = { mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null }
    else if (path === '/api/fields') json = [field]
    else if (path === '/api/farm') json = { name: 'Synthetic regression farm', season: 2026, total_area_ha: 1, defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] }
    else if (path === '/api/jobs/' + run) json = job
    else if (path === '/api/jobs') json = [job]
    else if (path.endsWith('/field-context')) {
      contexts.push(path)
      json = { schema_version: '0.1', field_id: field.id, revision: 2, geometry, centroid: [30, 50], area_ha: 1,
        region: { country_iso2: 'ZZ' }, locale: 'en', facts: [], timeseries_index: [], available_layers: [], available_charts: [] }
    } else if (path.endsWith('/health/ready')) return route.fulfill({ status: 503, json: { status: 'not_configured' } })
    else return route.fulfill({ status: 404, json: { detail: 'Not supplied by synthetic UI fixture' } })
    await route.fulfill({ json })
  })
  return contexts
}
test('agent integration preserves explicit year, scenario and trajectory through legacy redirect', async ({ page }) => {
  const contexts = await fixture(page)
  await page.goto('/agent?field=F-03&season=2028&scenario=less_rain&trajectory=reference&desktop=1')
  await expect.poll(() => contexts).toContain('/api/agent/assistant/F-03/2028/less_rain/field-context')
  const address = new URL(page.url())
  expect(address.pathname).toBe('/fields')
  expect(address.searchParams.get('season')).toBe('2028')
  expect(address.searchParams.get('scenario')).toBe('less_rain')
  expect(address.searchParams.get('trajectory')).toBe('reference')
  expect(contexts.every(path => path.includes('/2028/less_rain/'))).toBe(true)
})
test('agent integration sends the explicitly selected Analytics water run, not a current forecast', async ({ page }) => {
  const contexts = await fixture(page)
  await page.goto(`/analytics?section=water&field=F-03&season=2026&scenario=less_rain&job=${run}&desktop=1`)
  await page.getByRole('button', { name: 'Ask agent about this run', exact: true }).click()
  await expect.poll(() => contexts).toContain(`/api/agent/assistant/F-03/2026/less_rain/runs/${run}/field-context`)
  expect(contexts.every(path => path.includes(`/runs/${run}/`))).toBe(true)
})
