import { expect, test } from '@playwright/test'

const ring = [[30, 50], [30.01, 50], [30.01, 50.01], [30.004, 50.006], [30, 50.01], [30, 50]]
const hole = [[30.002, 50.002], [30.002, 50.004], [30.004, 50.004], [30.004, 50.002], [30.002, 50.002]]
const geometry = { type: 'Polygon', coordinates: [ring, hole] }
const supply = (west: number, east: number, value: number) => ({ value, geometry: {
  type: 'Polygon', coordinates: [[[west, 50], [east, 50], [east, 50.01], [west, 50.01], [west, 50]]],
} })

test('switching layers clears the previous thematic view and keeps the field mask', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('field-shift-desktop', '1'))
  await page.route('http://127.0.0.1:18112/api/**', async route => {
    const path = new URL(route.request().url())
    if (path.pathname === '/api/auth/session') return route.fulfill({ json: { mode: 'local', authenticated: true, role: 'admin', csrf_token: null } })
    if (path.pathname === '/api/fields') return route.fulfill({ json: [{ id: 'F-01', name: 'Map fixture', crop: 'Maize', area_ha: 42,
      boundary_revision: 1, geometry, planting: null }] })
    if (path.pathname === '/api/farm') return route.fulfill({ json: { name: 'Map fixture', season: 2026, total_area_ha: 42, defaults: {}, observations: [] } })
    if (path.pathname.endsWith('/terrain')) return route.fulfill({ json: { analysis: 'terrain', status: 'ready', field_id: 'F-01',
      generated_at: '2026-11-15T08:00:00Z', boundary_revision: 1, source: 'Fixture DEM', limitations: [],
      source_url: 'https://example.test/dem', source_resolution_m: 30, coverage_percent: 100, elevation_min_m: 100, elevation_max_m: 112,
      vectors: [{ start: [30.001, 50.001], end: [30.008, 50.005], slope_percent: 4, elevation_m: 108 }] } })
    if (path.pathname.endsWith('/moisture') && path.searchParams.get('source') === 'demo') {
      return route.fulfill({ json: { field_id: 'F-01', season: 2026, date: '2026-11-15', depth_cm: [0, 5], unit: 'm3/m3',
        demo: true, minimum: 0.15, maximum: 0.35, cells: [supply(30, 30.005, .15), supply(30.005, 30.01, .35)] } })
    }
    return route.fulfill({ json: path.pathname === '/api/jobs' || path.pathname.endsWith('/history') ? [] : null })
  })
  await page.goto('/fields?field=F-01&season=2026&layer=terrain&desktop=1', { waitUntil: 'domcontentloaded' })
  const terrain = page.locator('.leaflet-terrain-pane canvas')
  const raster = page.locator('.field-raster')
  await expect(terrain).toHaveCount(1)
  const picker = page.locator('.map-layer-picker')
  await picker.locator('summary').click()
  const radios = picker.getByRole('radiogroup')
  await expect(radios.locator('input[value="terrain"]')).toBeChecked()
  await expect(radios.locator('input:checked')).toHaveCount(1)
  await radios.locator('input[value="demo"]').check()
  await expect(terrain).toHaveCount(0)
  await expect(raster).toHaveCount(1)
  const mask = await raster.locator('clipPath:not([id$="-coverage"]) > path').getAttribute('d')
  expect(mask?.match(/M/g)).toHaveLength(2)
  await radios.locator('input[value="none"]').check()
  await expect(radios.locator('input:checked')).toHaveCount(1)
  await expect(raster).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
