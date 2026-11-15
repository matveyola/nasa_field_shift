/** Synthetic inputs exercise production routing, source changes and water warning display. */
import { expect, test, type Page } from '@playwright/test'
import { shiftDay } from '../../src/features/analytics/dashboardModel'
import type { DashboardSpan } from '../../src/api/fieldWeather'

const today = '2026-11-15'
const finalDay = shiftDay(today, 29)
const endOfForecast = shiftDay(today, 15)
const geometry = { type: 'Polygon', coordinates: [[[30, 50], [31, 50], [31, 51], [30, 50]]] }
const fields = [
  { id: 'north', name: 'North field', boundary_revision: 3 },
  { id: 'south', name: 'South field', boundary_revision: 8 },
].map(field => ({ ...field, crop: 'Maize', area_ha: 15, planting: null, geometry }))

function dataSpan(start: string, end: string, source: DashboardSpan['source'], value: number): DashboardSpan {
  return { start, end, source, reason: source ? null : 'unsupported', points: source ? [{ start, end, value }] : [] }
}

async function fixture(page: Page, { count = 5, risks = [] as number[], stale = false } = {}) {
  const calls: { field: string; revision: string | null; start: string | null }[] = []
  await page.clock.install({ time: new Date(today + 'T12:00:00Z') })
  await page.addInitScript(() => {
    sessionStorage.setItem('field-shift-desktop', '1')
    localStorage.setItem('field-shift-language', 'uk')
  })
  await page.route('http://127.0.0.1:18112/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/session')) return route.fulfill({ json: {
      mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null,
    } })
    if (url.pathname === '/api/fields') return route.fulfill({ json: fields })
    if (url.pathname === '/api/farm') return route.fulfill({ json: { name: 'Synthetic farm', season: 2026, total_area_ha: 30, defaults: {}, observations: [] } })
    if (url.pathname === '/api/jobs') return route.fulfill({ json: [] })
    if (url.pathname.endsWith('/weather-dashboard')) {
      const field = url.pathname.split('/')[3]
      const revision = url.searchParams.get('geometry_revision')
      const start = url.searchParams.get('start')!
      const end = url.searchParams.get('end')!
      calls.push({ field, revision, start })
      if (field === 'south') await new Promise(resolve => setTimeout(resolve, 400))
      const projection = dataSpan(shiftDay(endOfForecast, 1), end, 'climate', 2.5)
      const historical = end < today
      const rain = historical ? [dataSpan(start, end, 'reanalysis', 4)]
        : [dataSpan(start, endOfForecast, 'forecast', field === 'south' ? 10 : 1), projection]
      const unavailable = [dataSpan(start, end, null, 0)]
      return route.fulfill({ json: {
        field_id: field, boundary_revision: Number(revision), start, end, generated_at: today,
        series: { precipitation: rain, temperature_min: unavailable,
          temperature_max: historical ? [dataSpan(start, end, 'reanalysis', 13)] : [dataSpan(start, endOfForecast, 'forecast', 15), projection],
          reference_et0: unavailable, surface_moisture: historical ? [dataSpan(start, end, 'reanalysis', 0.34)] : unavailable },
      } })
    }
    if (url.pathname === '/api/water/forecast') {
      const id = url.searchParams.get('field_id')
      if (id !== 'north' || count === 0) return route.fulfill({ json: null })
      const days = Array.from({ length: count }, (_, i) => {
        const opening = risks.includes(i - 1) ? 20 : 90
        const reserve = risks.includes(i) ? 20 : 90
        const rain = 2 + Math.max(0, reserve - opening)
        return { date: shiftDay(today, i), reserve_mm: reserve, opening_mm: opening, rain_mm: rain,
          irrigation_mm: 0, drainage_mm: 0, water_use_mm: opening + rain - reserve, lead_days: i + 1 }
      })
      return route.fulfill({ json: {
        analysis: 'water_forecast', field_id: id, season: 2026, crop: 'Maize',
        initialized_at: stale ? '2026-11-11T00:00:00Z' : '2026-11-14T00:00:00Z',
        provider: 'Synthetic IFS', source_url: 'https://example.test', threshold_mm: 40, root_depth_m: 1,
        days, applicability: { status: 'not_assessed', source_variable: 'soil_temperature_0_to_7cm_mean',
          soil_temperature: days.map(({ date }) => ({ date, mean_c: 14 })) },
      } })
    }
    return route.fulfill({ status: 404, json: { detail: 'Not supplied by fixture' } })
  })
  return calls
}

test('requires a field, separates sources, and refreshes period and field without old values', async ({ page }) => {
  const calls = await fixture(page, { risks: [1, 2] })
  await page.goto('/analytics')
  await expect(page.getByText('Оберіть поле, щоб переглянути його дані.')).toBeVisible()
  expect(calls).toHaveLength(0)
  await page.getByRole('combobox', { name: 'Поле', exact: true }).selectOption('north')
  const notice = page.getByRole('region', { name: 'Посуха · найближчі 30 днів' })
  await expect(notice).toContainText('Ризик посухи')
  await expect(notice).toContainText('16 лист.')
  await expect(notice).toContainText('17 лист.')
  await expect(notice).toContainText('5/30')
  await expect(notice).not.toContainText('Найближчим часом посуха не очікується')
  const rain = page.getByRole('heading', { name: 'Опади · мм/добу' }).locator('xpath=ancestor::figure')
  await expect(rain).toContainText('Оперативний прогноз')
  await expect(rain).toContainText('Кліматичний сценарій')
  await rain.getByText('Значення та джерела', { exact: true }).click()
  await expect(rain.getByRole('link', { name: /EC-Earth3P/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Запас води в кореневому шарі · мм' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Водний баланс · мм' })).toBeVisible()
  await expect(page.getByText('Чистий баланс', { exact: true })).toBeVisible()
  expect(calls[0]).toEqual({ field: 'north', revision: '3', start: today })
  await page.getByRole('combobox', { name: 'Поле', exact: true }).selectOption('south')
  await expect(page.getByRole('heading', { name: 'Запас води в кореневому шарі · мм' })).toHaveCount(0)
  await expect(notice).toContainText('Немає актуального прогнозу')
  await expect.poll(() => calls.at(-1)).toEqual({ field: 'south', revision: '8', start: today })
  await page.getByLabel('Початок періоду').fill('2025-07-15')
  await page.getByLabel('Кінець періоду').fill('2025-07-15')
  await page.getByRole('button', { name: 'Застосувати' }).click()
  await expect(page.getByRole('region', { name: 'Посуха · вибраний період' })).toContainText('Для історичних дат')
  await expect(page.getByRole('heading', { name: 'Поверхнева вологість ґрунту · 0–7 см · м³/м³' })).toBeVisible()
  await expect.poll(() => calls.at(-1)).toEqual({ field: 'south', revision: '8', start: '2025-07-15' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('displays the clear text only with complete coverage and normalizes old rotation routes', async ({ page }) => {
  await fixture(page, { count: 30 })
  await page.goto('/analytics?section=rotation&field=north')
  await expect(page).toHaveURL(/section=overview/)
  await expect(page.getByText('Найближчим часом посуха не очікується')).toBeVisible()
  await expect(page.getByText('Історія сівозміни', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Кінець періоду')).toHaveValue(finalDay)
  await page.goto('/rotations?field=north')
  await expect(page).toHaveURL(/\/strategies\?/)
})

test('marks stale forecasts independently of weather data', async ({ page }) => {
  await fixture(page, { count: 30, stale: true })
  await page.goto('/analytics?field=north')
  await expect(page.getByText('Збережений прогноз застарів.')).toBeVisible()
  await expect(page.getByText('Найближчим часом посуха не очікується')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Опади · мм/добу' })).toBeVisible()
})
