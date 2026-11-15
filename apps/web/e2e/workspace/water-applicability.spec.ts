/** Explicit synthetic source contrasts; no field accuracy or provider execution claim. */
import { test, expect } from '@playwright/test'

const now = '2026-11-15T12:00:00Z'
for (const language of ['en', 'uk'] as const) for (const variant of ['cold', 'cold_below', 'zero', 'warm', 'legacy', 'unsupported'] as const) {
  test(`water reference interpretation remains honest for ${variant} source in ${language} on map and Analytics`, async ({ page }, testInfo) => {
    await page.clock.install({ time: new Date(now) })
    await page.addInitScript(language => { sessionStorage.setItem('field-shift-desktop', '1'); localStorage.setItem('field-shift-language', language) }, language)
    const text = (en: string, uk: string) => language === 'uk' ? uk : en
    const field = { id: 'F-01', name: 'Synthetic thermal contrast', crop: variant === 'unsupported' ? 'Rice' : 'Barley', area_ha: 1, boundary_revision: 0, planting: null,
      geometry: { type: 'Polygon', coordinates: [[[30,50],[30.01,50],[30.01,50.01],[30,50]]] } }
    const temperature = variant.startsWith('cold') ? -42.1 : variant === 'zero' ? 0 : 17
    const cold = temperature <= 0
    const reserve = variant === 'cold_below' ? 30 : 100
    const summary = reserve < 50
      ? text('Modeled reserve below reference threshold', 'Розрахунковий запас нижче довідкового порога')
      : text('Modeled reserve at or above reference threshold', 'Розрахунковий запас не нижче довідкового порога')
    const water = { analysis: 'water_forecast', field_id: field.id, season: 2026, crop: field.crop, initialized_at: '2026-11-14T00:00:00Z', generated_at: now,
      method: 'fao56', model_version: null, physical_model_version: 'synthetic-reference', provider: 'Synthetic IFS fixture', source_url: 'https://example.test',
      grid_resolution_km: 9, root_depth_m: 1, initial_reserve_mm: 100, capacity_mm: 150, threshold_mm: 50, field_capacity: .3, wilting_point: .15, crop_coefficient: 1.15,
      limitations: ['Synthetic test source, not measurements.'],
      applicability: variant === 'legacy' ? null : { status: cold ? 'review_required' : 'not_assessed', source_kind: 'forecast', source_variable: 'soil_temperature_0_to_7cm_mean',
        depth_cm: [0,7], unit: '°C', aggregation: 'daily_mean', initialized_at: '2026-11-14T00:00:00Z', soil_temperature: [{ date: '2026-11-15', mean_c: temperature }] },
      days: [{ date: '2026-11-15', reserve_mm: reserve, opening_mm: reserve, rain_mm: 0, irrigation_mm: 0, water_use_mm: 0, drainage_mm: 0, lead_days: 1, root_zone_estimate_mm: null }] }
    await page.route('http://127.0.0.1:18112/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let json: unknown = null
      if (path === '/api/auth/session') json = { mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null }
      else if (path === '/api/fields') json = [field]
      else if (path === '/api/farm') json = { name: 'Synthetic thermal test', season: 2026, total_area_ha: 1, defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] }
      else if (path === '/api/water/forecast') json = variant === 'unsupported' ? null : water
      else if (path === '/api/jobs') json = []
      else if (path.endsWith('/seasonal-water')) json = { field_id: field.id, geometry_revision: 0, rows: [] }
      else return route.fulfill({ status: 404, json: { detail: 'No synthetic response supplied' } })
      await route.fulfill({ json })
    })
    await page.goto('/fields?field=F-01&season=2026&desktop=1')
    const signal = page.locator('.field-water-assistant')
    if (variant === 'unsupported') {
      await expect(page.locator('.field-sheet-trigger')).not.toContainText('No water shortage expected')
      await expect(signal).not.toContainText('Modeled reserve')
      await page.goto('/analytics?section=water&field=F-01&season=2026&desktop=1')
      await expect(page.locator('.current-reserve dd')).toHaveText(text('No estimate', 'Немає оцінки'))
      await expect(page.locator('.current-field-overview')).toContainText(text('No current water estimate', 'Немає актуальної оцінки води'))
      await expect(page.locator('.water-today-reading')).toHaveCount(0)
      await page.getByText(text('Water controls, history and scenarios', 'Керування водою, історія та сценарії'), { exact: true }).click()
      await expect(page.locator('.water-forecast-panel')).toBeVisible()
      await expect(page.locator('.water-outlook')).toHaveCount(0)
      await expect(page.locator('.water-forecast-panel').getByRole('note')).toHaveCount(0)
      return
    }
    if (testInfo.project.name === 'mobile') {
      const trigger = page.locator('.field-sheet-trigger')
      await expect(trigger).toBeVisible()
      await expect(trigger).toContainText(summary)
      await expect(trigger).toHaveAttribute('aria-expanded', 'false')
      await expect(trigger).not.toContainText('No water shortage expected')
      if (cold) {
        await expect(trigger).toContainText('0–7')
        await expect(trigger).toHaveClass(/is-review/)
        await expect(trigger).toContainText(text('freezing is not modeled', 'замерзання не моделюється'))
      } else if (variant === 'legacy') await expect(trigger).toContainText(text('Temperature applicability unknown', 'Температурна застосовність невідома'))
      await trigger.click()
      await expect(signal.getByRole('note')).toBeVisible()
    }
    await expect(signal).toContainText(summary)
    await expect(signal).not.toContainText('No water shortage expected')
    await expect(signal).toContainText(text('does not model freezing, thawing or dormancy', 'не враховує замерзання, відтавання чи стан спокою'))
    if (cold) {
      await expect(signal).toContainText(text('Review required', 'Потрібна перевірка'))
      await expect(signal.locator('.is-review')).toHaveCount(1)
      await expect(signal.locator('.is-clear')).toHaveCount(0)
    } else if (variant === 'legacy') await expect(signal).toContainText(text('Temperature applicability is unknown', 'Температурна застосовність цього збереженого розрахунку невідома'))
    else await expect(signal).toContainText(text('do not establish crop suitability', 'не доводить придатності для культури'))
    await page.goto('/analytics?section=water&field=F-01&season=2026&desktop=1')
    const overview = page.locator('.current-field-overview')
    await expect(overview.locator('.current-reserve dd')).toHaveText(text(`${reserve} mm`, `${reserve} мм`))
    await expect(overview.getByRole('note')).toBeVisible()
    await expect(overview.getByRole('note')).toContainText(text('Freezing, thawing and dormancy are not modeled', 'Замерзання, відтавання і стан спокою не моделюються'))
    await expect(overview.getByRole('note')).toContainText(text('not measured liquid water or a crop-growth assessment', 'не є виміряною рідкою водою чи оцінкою росту культури'))
    if (cold) {
      await expect(overview.getByRole('note')).toContainText('0–7')
      await expect(overview.getByRole('note')).toContainText('≤0 °C')
      await expect(overview.getByRole('note')).toContainText(text('whole root zone is not established', 'всієї кореневої зони не доведено'))
    } else if (variant === 'legacy') await expect(overview.getByRole('note')).toContainText(text('Temperature applicability unknown', 'Температурна застосовність невідома'))
    else await expect(overview.getByRole('note')).toContainText(text('Crop suitability and liquid water are not assessed', 'Придатність для культури та рідку воду не оцінено'))
    await expect(overview).not.toContainText('No water shortage expected')
    const panel = page.locator('.field-water-block')
    // The compact overview now precedes one closed daily-evidence disclosure.
    // Preserve every original full-note, quantity and source assertion after opening it.
    await panel.getByText(text('Daily calculation and assumptions', 'Щоденний розрахунок і припущення'), { exact: true }).click()
    await expect(panel.locator('.water-today-reading')).toHaveText(text(`${reserve} mmmodeled reserve at day end`, `${reserve} ммрозрахунковий запас на кінець дня`))
    await expect(panel).toContainText(text('not a measurement of liquid plant-available water', 'не є вимірюванням доступної рослинам рідкої води'))
    if (cold) await expect(panel.getByRole('note')).toContainText(text('shallow 0–7 cm', 'поверхневому шарі 0–7 см'))
    else if (variant === 'legacy') await expect(panel.getByRole('note')).toContainText(text('no shallow-soil diagnostic was stored', 'діагностику поверхневого ґрунту не збережено'))
    else await expect(panel.getByRole('note')).toContainText(text('do not establish crop suitability', 'не доводить придатності для культури'))
    if (variant !== 'legacy') {
      await expect(panel).toContainText('soil_temperature_0_to_7cm_mean')
      await expect(panel).toContainText(`${new Intl.NumberFormat(language === 'uk' ? 'uk-UA' : 'en-US', {maximumFractionDigits: 1}).format(temperature)} °C`)
    }
    await page.getByText(text('Water controls, history and scenarios', 'Керування водою, історія та сценарії'), { exact: true }).click()
    const detailed = page.locator('.water-forecast-panel')
    await expect(detailed).toBeVisible()
    await expect(detailed.locator('.water-outlook')).toContainText(summary)
    await expect(detailed).not.toContainText('No water shortage expected')
    await expect(detailed.getByRole('note')).toBeVisible()
    await expect(detailed.getByRole('note')).toContainText(text('does not model freezing, thawing or dormancy', 'не враховує замерзання, відтавання чи стан спокою'))
    if (reserve < 50) await expect(detailed.locator('.water-outlook time')).toHaveAttribute('dateTime', '2026-11-15')
    if (cold) {
      await expect(detailed.locator('.water-outlook')).toHaveAttribute('data-status', 'review')
      await expect(detailed.getByRole('note')).toContainText('0–7')
    } else if (variant === 'legacy') await expect(detailed.getByRole('note')).toContainText(text('Temperature applicability is unknown', 'Температурна застосовність цього збереженого розрахунку невідома'))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
