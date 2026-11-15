import { test, expect, type Page } from '@playwright/test'
import { catalogFixture, resultFixture } from './handoffFixtures'
import type { StrategyCandidate, StrategyScenario, StrategySubmission } from '../../src/api/rotationHandoff'

// Intercepted application responses exercise the UI without claiming model accuracy.
async function setup(page: Page, ukrainian = false) {
  const catalog = catalogFixture()
  catalog.operations = ['climate_step']
  const cropIds = catalog.crops.map(crop => crop.id)
    .filter(id => id !== 'wheat_winter_nonfrozen' && id !== 'wheat_spring').slice(0, 10)
  catalog.crops = catalog.crops.map(crop => ({ ...crop, strategy_supported: cropIds.includes(crop.id) }))
  const posts: StrategySubmission[] = []
  const fieldRequests: string[] = []
  const saves: unknown[] = []
  let saved: StrategyScenario | undefined
  function candidate(crop: string, index: number): StrategyCandidate {
    return { crop_id: crop, rank: index + 1, status: 'ok', water: { water_deficit_pct: index * 3, water_stress_days_pct: null },
      economics: { currency: 'USD', gross_revenue_per_ha: 30 + index, revenue_after_seed_per_ha: 20 + index } }
  }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/handoff/catalog')) return route.fulfill({ json: catalog })
    const fieldId = path.split('/')[5]
    const revision = fieldId === 'north' ? 4 : 6
    if (path.endsWith('/strategy-context')) {
      fieldRequests.push(fieldId)
      return route.fulfill({ json: { field_id: fieldId, geometry_revision: revision, area_ha: 20, min_year: 2040, max_year: 2049,
        min_month: 3, max_month: 7, max_steps: 6, max_calendar_years: 10, history: [{ crop_id: 'wheat_spring', sowing_date: fieldId === 'north' ? '2039-04-03' : null, season_year: 2039 }] } })
    }
    if (path.endsWith('/strategy-jobs')) {
      const input: StrategySubmission = route.request().postDataJSON()
      posts.push(input)
      const choices = input.comparison_crops ?? cropIds.slice(0, input.previous.length ? 3 : 10)
      const rows = choices.map(candidate)
      const result = { ...resultFixture(), operation: 'climate_step', data_kind: 'conditional_climate', payload: {
        candidates: rows, top_candidates: rows, ranking: { status: input.priority === 'water_stress' ? 'priority_unavailable' : 'ranked' },
        weather_dataset: 'Synthetic conditional weather',
      } }
      return route.fulfill({ json: { job_id: `job-${posts.length}`, field_id: fieldId, geometry_revision: revision, status: 'succeeded', attempts: 1, result, error: null } })
    }
    if (path.endsWith('/strategy-scenarios')) {
      if (route.request().method() === 'GET') return route.fulfill({ json: saved && saved.field_id === fieldId ? [saved] : [] })
      const body = route.request().postDataJSON()
      saves.push(body)
      saved = { id: 'saved-1', field_id: fieldId, geometry_revision: revision, created_at: '2040-04-30T00:00:00Z',
        priority: 'water_deficit', irrigation: { status: 'unknown' }, assumptions: ['Synthetic scenario'],
        choices: body.choices.map(({ job_id, crop_id }: { job_id: string; crop_id: string }, index: number) => ({
          job_id, crop_id, planting_date: posts[Number(job_id.slice(4)) - 1].planting_date, metrics: candidate(crop_id, index),
        })) }
      return route.fulfill({ json: saved })
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected fixture route' } })
  })
  await page.goto('/tests/strategies/workflows.html')
  if (ukrainian) await page.getByRole('combobox', { name: 'Language' }).selectOption('uk')
  return { posts, fieldRequests, saves, cropIds }
}

test('select a field, recalculate edited steps, and save only the completed sequence', async ({ page }) => {
  const { posts, fieldRequests, saves, cropIds } = await setup(page)
  await expect(page.getByText('Choose a field to plan a rotation or compare crops.')).toBeVisible()
  expect(fieldRequests).toEqual([])
  await page.getByLabel('Field in focus').selectOption('north')
  await expect(page.getByRole('group', { name: 'Planning workflow' }).getByRole('button')).toHaveCount(2)
  const sequence = page.locator('#strategy-sequence')
  await expect(sequence.getByText('Previous sowing date', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Previous sowing date, if known')).toHaveCount(0)
  await page.getByLabel('Number of steps').selectOption('2')
  await page.getByRole('button', { name: 'Calculate this step' }).click()
  await expect(page.getByText('Available crops: 10 of 10')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save completed scenario' })).toHaveCount(0)
  expect(posts[0]).toMatchObject({ geometry_revision: 4, mode: 'sequence', previous: [], irrigation: { status: 'unknown' } })
  expect(posts[0]).not.toHaveProperty('history_sowing_date')
  await page.getByRole('button', { name: /^Choose / }).first().click()
  await expect(page.getByText('Step 2 of 2')).toBeVisible()
  expect(posts).toHaveLength(1)
  await page.getByRole('button', { name: 'Calculate this step' }).click()
  await expect(page.getByText('Available crops: 3 of 10')).toBeVisible()
  expect(posts[1].previous).toEqual([{ job_id: 'job-1', crop_id: cropIds[0] }])
  await page.getByRole('button', { name: 'Change step 1' }).click()
  await expect(page.getByText('Step 1 of 2')).toBeVisible()
  await expect(page.getByText('Available crops: 3 of 10')).toHaveCount(0)
  await page.getByRole('button', { name: 'Calculate this step' }).click()
  await page.getByRole('button', { name: /^Choose / }).nth(1).click()
  await page.getByRole('button', { name: 'Calculate this step' }).click()
  await page.getByRole('button', { name: /^Choose / }).first().click()
  expect(saves).toHaveLength(0)
  await page.getByRole('button', { name: 'Save completed scenario' }).click()
  await expect(page.getByText('Scenario saved', { exact: true })).toBeVisible()
  expect(saves).toEqual([{ geometry_revision: 4, choices: [{ job_id: 'job-3', crop_id: cropIds[1] }, { job_id: 'job-4', crop_id: cropIds[0] }] }])
  await page.getByText('Saved scenarios', { exact: true }).click()
  await expect(page.locator('.strategy-saved > details > summary')).toContainText(' → ')
  await page.getByLabel('Field in focus').selectOption('south')
  await expect(page.getByText('Completed scenario')).not.toBeVisible()
  await expect(sequence.getByLabel('Previous sowing date, if known')).toBeVisible()
  expect(fieldRequests).toEqual(['north', 'south'])
})

test('compare only two crops with a shared previous sowing date and optional water volume', async ({ page }) => {
  const { posts, cropIds } = await setup(page, true)
  await page.getByLabel('Обране поле').selectOption('south')
  await page.getByRole('button', { name: 'Порівняти дві культури' }).click()
  await expect(page.getByRole('heading', { name: 'Порівняння двох культур' })).toBeVisible()
  const panel = page.locator('#strategy-compare')
  await panel.getByLabel('Перша культура').selectOption(cropIds[0])
  await panel.getByLabel('Друга культура').selectOption(cropIds[1])
  await panel.getByLabel('Дата попередньої сівби, якщо відома').fill('2039-04-01')
  await panel.getByLabel('Пріоритети').selectOption('revenue_after_seed')
  await expect(panel.getByText('Виручка мінус вартість насіння або посадкового матеріалу, на гектар.')).toBeVisible()
  await panel.getByText('Полив (необов’язково)').click()
  await panel.getByLabel('Доступність поливу').selectOption('available')
  await panel.getByLabel('Доступна вода, м³/добу').fill('400')
  await expect(panel.getByText(/Еквівалентна глибина для поля: 2.*мм\/добу/)).toBeVisible()
  await panel.getByRole('button', { name: 'Порівняти ці культури' }).click()
  await expect(panel.locator('.strategy-candidate')).toHaveCount(2)
  await expect(panel.getByText(/Різниця \(друга культура мінус перша\): \+1.*USD\/га/)).toBeVisible()
  expect(posts[0]).toMatchObject({ geometry_revision: 6, mode: 'compare', step_count: 1, previous: [],
    history_sowing_date: '2039-04-01', comparison_crops: cropIds.slice(0, 2), irrigation: { status: 'available', daily_m3: 400 } })
  const bounds = await panel.locator('.strategy-comparison').evaluate(root => Array.from(root.children).map(card => card.getBoundingClientRect().right))
  expect(bounds.every(right => right <= page.viewportSize()!.width)).toBe(true)
})
