/** Synthetic API boundaries exercise the real App, routing and onboarding together. */
import { test, expect } from '@playwright/test'
const id = 'fe1d4840-3928-4d02-8a09-67f883e0245b'
const geometry = { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] }
for (const inspectionYear of [2024, 2028]) {
  test(`setup uses active farm history when deep-linked from ${inspectionYear}`, async ({ page }) => {
    await page.addInitScript(({ id, geometry }) => {
      sessionStorage.setItem('field-shift-desktop', '1')
      localStorage.setItem('field-shift:setup:' + id, JSON.stringify({
        step: 'chat', name: 'Test field', location: { countryCode: 'UA', regionCode: 'all' }, geometry, answers: {},
      }))
    }, { id, geometry })
    let submitted: { start_season: number; history: { season: number; crop: string }[] } | undefined
    await page.route('http://127.0.0.1:18112/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let json: unknown = null
      if (path.endsWith('/auth/session')) json = { mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null }
      else if (path === '/api/fields') json = []
      else if (path === '/api/farm') json = { name: 'Synthetic regression farm', season: 2026, total_area_ha: 0, defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] }
      else if (path === '/api/onboarding/' + id && route.request().method() === 'GET') return route.fulfill({ status: 404, json: { detail: 'No saved setup' } })
      else if (path === '/api/onboarding/' + id && route.request().method() === 'PUT') {
        submitted = route.request().postDataJSON()
        json = { id, status: 'draft', inputs: submitted, field: null, demo_plan: null }
      }
      await route.fulfill({ json })
    })
    await page.goto(`/onboarding?setup=${id}&season=${inspectionYear}&desktop=1`)
    await expect(page.getByText('What grew in this field in the 2026 season?')).toBeVisible()
    await expect(page.getByText(`What grew in this field in the ${inspectionYear} season?`)).toHaveCount(0)
    await page.getByRole('button', { name: 'Maize', exact: true }).click()
    await expect.poll(() => submitted?.start_season).toBe(2027)
    expect(submitted?.history).toEqual([{ season: 2026, crop: 'Maize' }])
  })
}

for (const { farmYear, savedStart } of [{ farmYear: 2100, savedStart: 2100 }, { farmYear: 2026, savedStart: 2030 }]) {
  test(`completed setup preserves saved planning year ${savedStart} for farm ${farmYear}`, async ({ page }) => {
    const field = { id: 'new-field', name: 'Completed synthetic field', crop: 'Maize', area_ha: 1, geometry, boundary_revision: 0, planting: null }
    await page.addInitScript(() => sessionStorage.setItem('field-shift-desktop', '1'))
    await page.route('http://127.0.0.1:18112/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let json: unknown = null
      if (path.endsWith('/auth/session')) json = { mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null }
      else if (path === '/api/fields') json = []
      else if (path === '/api/farm') json = { name: 'Synthetic regression farm', season: farmYear, total_area_ha: 0, defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] }
      else if (path === '/api/onboarding/' + id) json = { id, status: 'completed', inputs: { start_season: savedStart, country_code: 'UA', region_code: 'all', geometry }, field, demo_plan: null }
      await route.fulfill({ json })
    })
    await page.goto(`/onboarding?setup=${id}&desktop=1`)
    await expect.poll(() => new URL(page.url()).pathname).toBe('/strategies')
    expect(new URL(page.url()).searchParams.get('season')).toBe(String(savedStart))
    expect(new URL(page.url()).searchParams.get('field')).toBe('new-field')
  })
}
