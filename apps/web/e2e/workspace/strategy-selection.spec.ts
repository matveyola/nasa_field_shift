/** Synthetic API responses verify the real App's route and field-selection behavior. */
import { expect, test } from '@playwright/test'

test('Strategies starts without selecting a field and legacy links reach the two workflows', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('field-shift-desktop', '1'))
  const contextFields: string[] = []
  const geometry = { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] }
  const fields = ['north', 'south'].map((id, index) => ({ id, name: `${id} field`, crop: 'Maize', area_ha: 12,
    geometry, boundary_revision: index + 1, planting: null }))
  await page.route('http://127.0.0.1:18112/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/session') return route.fulfill({ json: { mode: 'local', authenticated: true, role: 'admin', csrf_token: null } })
    if (path === '/api/fields') return route.fulfill({ json: fields })
    if (path === '/api/farm') return route.fulfill({ json: { name: 'Synthetic route test', season: 2026, total_area_ha: 24,
      defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] } })
    if (path.endsWith('/strategy-context')) {
      const field = fields.find(row => path.includes(`/${row.id}/`))!
      contextFields.push(field.id)
      return route.fulfill({ json: { field_id: field.id, geometry_revision: field.boundary_revision, area_ha: field.area_ha,
        min_year: 2040, max_year: 2049, min_month: 3, max_month: 7, max_steps: 6, max_calendar_years: 10, history: [] } })
    }
    if (path.endsWith('/handoff/catalog')) return route.fulfill({ json: { status: 'unavailable', operations: [], crops: [], reasons: ['Synthetic model unavailable'] } })
    return route.fulfill({ json: null })
  })
  await page.goto('/strategies?desktop=1')
  await expect(page.getByRole('combobox', { name: 'Field in focus' })).toHaveValue('')
  await expect(page.getByText('Choose a field to continue.')).toBeVisible()
  expect(contextFields).toEqual([])
  await page.goto('/rotations?planning=1&field=south&desktop=1')
  await expect.poll(() => new URL(page.url()).pathname).toBe('/strategies')
  await expect(page.getByRole('combobox', { name: 'Field in focus' })).toHaveValue('south')
  await expect(page.getByRole('tablist').getByRole('tab')).toHaveText(['Quick crop comparison', 'Crop rotations'])
  await expect(page.getByRole('tabpanel', { name: 'Quick crop comparison' }).getByLabel('Sowing date', { exact: true })).toBeVisible()
  await expect(page.getByText('The rotation model is temporarily unavailable.')).toBeVisible()
  expect([...new Set(contextFields)]).toEqual(['south'])
})
