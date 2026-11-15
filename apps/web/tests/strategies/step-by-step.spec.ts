import { test, expect, type Page } from '@playwright/test'
import { catalogFixture, resultFixture } from './handoffFixtures'
import type { StrategyCandidate, StrategyScenario, StrategySubmission } from '../../src/api/rotationHandoff'

// Intercepted application responses exercise the UI without claiming model accuracy.
async function setup(page: Page, { ukrainian = false, modelAvailable = true } = {}) {
  const catalog = catalogFixture()
  catalog.operations = modelAvailable ? ['climate_step'] : []
  if (!modelAvailable) catalog.status = 'unavailable'
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
        min_month: 3, max_month: 7, max_steps: 6, max_calendar_years: 10,
        history: fieldId === 'north' ? [{ crop_id: 'wheat_spring', sowing_date: '2039-04-03', season_year: 2039 }] : [] } })
    }
    if (path.endsWith('/strategy-jobs')) {
      const input: StrategySubmission = route.request().postDataJSON()
      posts.push(input)
      const choices = cropIds.slice(0, input.previous.length ? 3 : 10)
      const rows = choices.map(candidate)
      const result = { ...resultFixture(), operation: 'climate_step', data_kind: 'conditional_climate', ranking_usable: fieldId === 'north', payload: {
        candidates: rows, top_candidates: rows, ranking: { status: input.priority === 'water_stress' ? 'priority_unavailable' : 'ranked', selection_ids: choices },
        weather_dataset: 'Synthetic conditional weather',
        observed_previous_crop: fieldId === 'north' ? { crop_id: 'wheat_spring' } : null,
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
  await expect(page.getByText('Choose a field to continue.')).toBeVisible()
  expect(fieldRequests).toEqual([])
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('north')
  await expect(page.getByRole('tablist').getByRole('tab')).toHaveCount(2)
  await page.getByRole('tab', { name: 'Scenarios' }).click()
  const sequence = page.getByRole('tabpanel', { name: 'Scenarios' })
  await sequence.getByText('Create a scenario', { exact: true }).click()
  await expect(sequence.getByLabel('Previous sowing date, if known')).toHaveCount(0)
  await sequence.getByLabel('Number of steps').selectOption('2')
  await sequence.getByLabel('Sowing date').fill('2040-05-01')
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
  await expect(page.locator('.strategy-saved > details > summary')).toContainText(' → ')
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('south')
  await expect(page.getByText('Completed scenario')).not.toBeVisible()
  await expect(sequence.getByText('No saved scenarios yet.')).toBeVisible()
  expect(fieldRequests).toEqual(['north', 'south'])
})

test('the date tab works without a passport and keeps advanced inputs in Scenarios', async ({ page }) => {
  const { posts } = await setup(page, { ukrainian: true })
  await page.getByRole('combobox', { name: 'Обране поле' }).selectOption('south')
  const tabs = page.getByRole('tablist').getByRole('tab')
  await expect(tabs).toHaveText(['Дата сівби', 'Сценарії'])
  const datePanel = page.getByRole('tabpanel', { name: 'Дата сівби' })
  const input = datePanel.getByLabel('Дата сівби')
  await expect(input).toHaveValue('')
  await expect(datePanel.locator('form').locator('input, select')).toHaveCount(1)
  await input.fill('2040-10-01')
  await expect(datePanel.getByRole('button', { name: 'Розрахувати', exact: true })).toBeDisabled()
  await input.fill('2040-04-01')
  await datePanel.getByRole('button', { name: 'Розрахувати', exact: true }).click()
  await expect(datePanel.locator('.strategy-candidate')).toHaveCount(10)
  await expect(datePanel.getByText('Оцінку виконано за невідомої попередньої культури.')).toBeVisible()
  await expect(datePanel.getByText('Модель розрахувала показники культур без рейтингу.')).toBeVisible()
  expect(posts[0]).toMatchObject({ geometry_revision: 6, mode: 'sequence', planting_date: '2040-04-01',
    step_count: 1, previous: [], irrigation: { status: 'unknown' } })
  expect(posts[0]).not.toHaveProperty('history_crop_id')
  expect(posts[0]).not.toHaveProperty('history_sowing_date')
  await tabs.first().focus()
  await page.keyboard.press('ArrowRight')
  await expect(tabs.last()).toBeFocused()
  const panel = page.getByRole('tabpanel', { name: 'Сценарії' })
  await panel.getByText('Створити сценарій', { exact: true }).click()
  await panel.getByLabel('Дата сівби').fill('2040-04-01')
  await panel.getByLabel('Пріоритети').selectOption('revenue_after_seed')
  await expect(panel.getByText('Виручка мінус вартість насіння або посадкового матеріалу, на гектар.')).toBeVisible()
  await panel.getByText('Полив (необов’язково)').click()
  await panel.getByLabel('Доступність поливу').selectOption('available')
  await panel.getByLabel('Доступна вода, м³/добу').fill('400')
  await expect(panel.getByText(/Еквівалентна глибина для поля: 2.*мм\/добу/)).toBeVisible()
  await panel.getByRole('button', { name: 'Розрахувати цей крок' }).click()
  await expect(panel.locator('.strategy-candidate')).toHaveCount(10)
  expect(posts[1]).toMatchObject({ priority: 'revenue_after_seed', irrigation: { status: 'available', daily_m3: 400 } })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await tabs.last().press('ArrowLeft')
  await expect(input).toHaveValue('2040-04-01')
})

test('saved scenarios open during a model outage', async ({ page }) => {
  await setup(page, { modelAvailable: false })
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('south')
  await expect(page.getByText('The rotation model is temporarily unavailable.')).toBeVisible()
  await page.getByRole('tab', { name: 'Scenarios' }).click()
  await expect(page.getByRole('heading', { name: 'Saved scenarios' })).toBeVisible()
  await expect(page.getByText('No saved scenarios yet.')).toBeVisible()
})
