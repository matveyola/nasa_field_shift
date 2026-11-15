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
  const hybridId = 'maize-p0987amx'
  const hybridOptions = [
    { choice_id: hybridId, crop_id: 'maize_grain', name: 'Pioneer P0987AMX', source_url: 'https://example.org/trial' },
    { choice_id: 'maize-p1197amxt', crop_id: 'maize_grain', name: 'Pioneer P1197AMXT', source_url: 'https://example.org/trial' },
  ]
  catalog.strategy_hybrids = hybridOptions
  function candidate(choice: string, index: number, planting: string): StrategyCandidate {
    const hybrid = hybridOptions.find(row => row.choice_id === choice)
    const end = planting.startsWith('2040-05') ? '2040-06-05' : `${planting.slice(0, 4)}-09-04`
    const recovery = planting.startsWith('2040-05') ? '2040-06-15' : `${planting.slice(0, 4)}-10-20`
    return {
      crop_id: hybrid?.crop_id ?? choice, choice_id: choice, rank: index + 1, status: 'ok',
      ...(hybrid ? { choice: { hybrid } } : {}),
      water: { water_deficit_pct: index * 3, water_stress_days_pct: null },
      economics: { currency: 'USD', gross_revenue_per_ha: 30 + index, revenue_after_seed_per_ha: 20 + index },
      scenarios: { test: { end_date: end, post_crop_recharge: { status: 'reached', target_reached_on: recovery } } },
    }
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
      const choices = input.comparison_crops ?? [...cropIds.slice(0, input.previous.length ? 3 : 10), hybridId]
      const rows = choices.map((id, index) => candidate(id, index, input.planting_date))
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
        choices: body.choices.map((choice: StrategyScenario['choices'][number], index: number) => {
          const planting_date = posts[Number(choice.job_id.slice(4)) - 1].planting_date
          return { ...choice, planting_date, metrics: candidate(choice.choice_id ?? choice.crop_id, index, planting_date) }
        }) }
      return route.fulfill({ json: saved })
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected fixture route' } })
  })
  await page.goto('/tests/strategies/workflows.html')
  if (ukrainian) await page.getByRole('combobox', { name: 'Language' }).selectOption('uk')
  return { posts, fieldRequests, saves, cropIds, hybridId }
}

test('the second and third crops are assessed automatically and the completed rotation can be saved', async ({ page }) => {
  const { posts, fieldRequests, saves, cropIds, hybridId } = await setup(page)
  await expect(page.getByText('Choose a field to continue.')).toBeVisible()
  expect(fieldRequests).toEqual([])
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('north')
  await page.getByRole('tab', { name: 'Crop rotations' }).click()
  const rotation = page.getByRole('tabpanel', { name: 'Crop rotations' })
  const count = rotation.getByLabel('Seasons in the rotation', { exact: true })
  const assess = rotation.getByRole('button', { name: 'Assess crops for the rotation' })
  const choose = rotation.getByRole('button', { name: /^Choose / })
  const chooseHybrid = rotation.getByRole('button', { name: /Choose .*Pioneer P0987AMX/ })
  const save = rotation.getByRole('button', { name: 'Save crop rotation' })

  await rotation.getByText('Plan a crop rotation', { exact: true }).click()
  await count.selectOption('3')
  await rotation.getByLabel('Sowing date', { exact: true }).fill('2040-05-01')
  await assess.click()
  await expect(rotation.getByText('Available options: 11 of 11')).toBeVisible()
  expect(posts[0]).toMatchObject({ geometry_revision: 4, mode: 'sequence', previous: [], irrigation: { status: 'unknown' } })

  await chooseHybrid.click()
  await expect(rotation.getByText('Season 2 of 3 in the rotation')).toBeVisible()
  await expect(count).toHaveCount(0)
  await expect(assess).toHaveCount(0)
  await expect(rotation.getByText('Available options: 4 of 4')).toBeVisible()
  await expect(rotation.getByText(/Suggested sowing date: Jun 16, 2040.*Jun 15, 2040/)).toBeVisible()
  expect(posts[1]).toMatchObject({ step_count: 3, priority: 'water_deficit', planting_date: '2040-06-16',
    irrigation: { status: 'unknown' }, previous: [{ job_id: 'job-1', crop_id: 'maize_grain', choice_id: hybridId }] })

  await rotation.getByRole('button', { name: 'Change crop 1 in the rotation' }).click()
  await expect(rotation.getByText('Season 1 of 3 in the rotation')).toBeVisible()
  await expect(rotation.getByText('Available options: 4 of 4')).toHaveCount(0)
  await assess.click()
  await chooseHybrid.click()
  await choose.first().click()
  await expect(rotation.getByText('Season 3 of 3 in the rotation')).toBeVisible()
  await expect(rotation.getByText('Available options: 4 of 4')).toBeVisible()
  await expect(rotation.getByText(/first day/)).toBeVisible()
  expect(posts[4]).toMatchObject({ planting_date: '2041-03-01', previous: [
    { job_id: 'job-3', crop_id: 'maize_grain', choice_id: hybridId }, { job_id: 'job-4', crop_id: cropIds[0] },
  ] })
  await rotation.getByText('Change sowing date', { exact: true }).click()
  await rotation.getByLabel('Sowing date', { exact: true }).fill('2041-04-01')
  await expect(rotation.getByText(/changed the date manually/)).toBeVisible()
  await rotation.getByRole('button', { name: 'Recalculate for this date' }).click()
  await expect(rotation.getByText('Available options: 4 of 4')).toBeVisible()
  expect(posts[5]).toMatchObject({ planting_date: '2041-04-01' })
  await expect(save).toHaveCount(0)
  await choose.first().click()
  expect(posts).toHaveLength(6)
  expect(saves).toHaveLength(0)
  await save.click()
  await expect(rotation.getByText('Crop rotation saved', { exact: true })).toBeVisible()
  expect(saves).toEqual([{ geometry_revision: 4, choices: [
    { job_id: 'job-3', crop_id: 'maize_grain', choice_id: hybridId }, { job_id: 'job-4', crop_id: cropIds[0] }, { job_id: 'job-6', crop_id: cropIds[0] },
  ] }])
  await expect(page.locator('.strategy-saved > details > summary')).toContainText('Pioneer P0987AMX → ')
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('south')
  await expect(rotation.getByText('Completed crop rotation')).not.toBeVisible()
  await expect(rotation.getByText('No saved crop rotations yet.')).toBeVisible()
  expect(fieldRequests).toEqual(['north', 'south'])
})

test('quick comparison requires two crops and explains valid months in Ukrainian', async ({ page }) => {
  const { posts, cropIds } = await setup(page, { ukrainian: true })
  await page.getByRole('combobox', { name: 'Обране поле' }).selectOption('south')
  const tabs = page.getByRole('tablist').getByRole('tab')
  await expect(tabs).toHaveText(['Швидке порівняння культур', 'Сівозміни'])
  const datePanel = page.getByRole('tabpanel', { name: 'Швидке порівняння культур' })
  const input = datePanel.getByLabel('Дата сівби', { exact: true })
  const compare = datePanel.getByRole('button', { name: 'Порівняти культури', exact: true })
  await expect(input).toHaveValue('')
  await expect(datePanel.getByText(/Місяці сівби для розрахунку: березень – липень.*перезимівля/)).toBeVisible()
  await datePanel.getByLabel('Перша культура').selectOption(cropIds[0])
  await expect(compare).toBeDisabled()
  await datePanel.getByLabel('Друга культура').selectOption(cropIds[1])
  await datePanel.getByLabel('Критерій порівняння').selectOption('revenue')
  await datePanel.getByRole('button', { name: 'Пояснення: Виручка' }).click()
  await expect(datePanel.getByRole('dialog')).toContainText('Довідкова врожайність із гектара')
  await page.keyboard.press('Escape')
  await input.fill('2040-10-01')
  await expect(compare).toBeDisabled()
  await input.fill('2040-04-01')
  await compare.click()
  await expect(datePanel.locator('.strategy-candidate')).toHaveCount(2)
  await expect(datePanel.getByText('За цих умов рейтинг за обраним критерієм недоступний.')).toBeVisible()
  expect(posts[0]).toMatchObject({ geometry_revision: 6, mode: 'compare', planting_date: '2040-04-01',
    step_count: 1, priority: 'revenue', comparison_crops: [cropIds[0], cropIds[1]], previous: [], irrigation: { status: 'unknown' } })
  await tabs.first().focus()
  await page.keyboard.press('ArrowRight')
  await expect(tabs.last()).toBeFocused()
  const panel = page.getByRole('tabpanel', { name: 'Сівозміни' })
  await panel.getByText('Спланувати сівозміну', { exact: true }).click()
  await panel.getByLabel('Дата сівби', { exact: true }).fill('2040-04-01')
  await panel.getByLabel('Пріоритет').selectOption('revenue_after_seed')
  await expect(panel.getByText('Виручка мінус вартість насіння або посадкового матеріалу, на гектар.')).toBeVisible()
  await panel.getByText('Полив (необов’язково)').click()
  await panel.getByLabel('Доступність поливу').selectOption('available')
  await panel.getByLabel('Доступна вода, м³/добу').fill('400')
  await expect(panel.getByText(/Еквівалентна глибина для поля: 2.*мм\/добу/)).toBeVisible()
  await panel.getByRole('button', { name: 'Оцінити культури для сівозміни' }).click()
  await expect(panel.locator('.strategy-candidate')).toHaveCount(11)
  expect(posts[1]).toMatchObject({ priority: 'revenue_after_seed', irrigation: { status: 'available', daily_m3: 400 } })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await tabs.last().press('ArrowLeft')
  await expect(input).toHaveValue('2040-04-01')
})

test('saved scenarios open during a model outage', async ({ page }) => {
  await setup(page, { modelAvailable: false })
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('south')
  await expect(page.getByText('The rotation model is temporarily unavailable.')).toBeVisible()
  await page.getByRole('tab', { name: 'Crop rotations' }).click()
  await expect(page.getByRole('heading', { name: 'Saved crop rotations' })).toBeVisible()
  await expect(page.getByText('No saved crop rotations yet.')).toBeVisible()
})

test('quick comparison can show distinct results for hybrids of the same crop', async ({ page }) => {
  const { posts } = await setup(page)
  await page.getByRole('combobox', { name: 'Field in focus' }).selectOption('north')
  const panel = page.getByRole('tabpanel', { name: 'Quick crop comparison' })
  const first = panel.getByRole('combobox', { name: 'First crop', exact: true })
  const second = panel.getByRole('combobox', { name: 'Second crop', exact: true })
  await first.selectOption('maize-p0987amx')
  await expect(second.locator('option[value=maize-p0987amx]')).toBeDisabled()
  await second.selectOption('maize-p1197amxt')
  await panel.getByLabel('Sowing date', { exact: true }).fill('2040-05-01')
  await panel.getByRole('button', { name: 'Compare crops', exact: true }).click()
  const cards = panel.locator('.strategy-candidate')
  await expect(cards.nth(0)).toContainText('Pioneer P0987AMX')
  await expect(cards.nth(0)).toContainText('0 %')
  await expect(cards.nth(1)).toContainText('Pioneer P1197AMXT')
  await expect(cards.nth(1)).toContainText('3 %')
  expect(posts[0].comparison_crops).toEqual(['maize-p0987amx', 'maize-p1197amxt'])
})
