import { test, expect } from '../../../../services/agent/frontend/node_modules/@playwright/test/index.mjs'
import { cases, expectedStatus } from '../../tests/planning/fixtures'

const base = '/e2e/planning-v1/index.html'
async function open(page, testInfo, query = '') {
  await page.goto(`${base}?${query}${testInfo.project.name === 'phone-preview' ? '&phone=1' : ''}`)
  return testInfo.project.name === 'phone-preview' ? page.frameLocator('iframe[title="Planning phone preview"]') : page
}

for (const spec of cases) test(`${spec.id}: geographic evidence is labeled synthetic and never silently eligible`, async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const root = await open(page, testInfo, `case=${spec.id}`)
  await expect(root.getByRole('heading', { name: 'Crop planning', exact: true })).toBeVisible()
  await expect(root.getByText('Synthetic demonstration — not measured accuracy', { exact: true })).toBeVisible()
  await expect(root.getByText(expectedStatus[spec.id], { exact: true }).first()).toBeVisible()
  await expect(root.getByText(`[${spec.id}] ${spec.title} — synthetic UI response, not a model result.`, { exact: true }).first()).toBeVisible()
  if (expectedStatus[spec.id] !== 'eligible') await expect(root.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeDisabled()
  const main = root.locator('main')
  expect(await main.evaluate(el => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  expect(errors).toEqual([])
  if (spec.id === 'G23') {
    await page.screenshot({ path: testInfo.outputPath('workspace.png') })
    await root.getByRole('table').screenshot({ path: testInfo.outputPath('season-matrix.png') })
  }
})

test('keyboard matrix navigation, evidence intervals and unknown margin', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23')
  const first = root.getByRole('button', { name: 'G23 2027 Wheat', exact: true })
  await first.focus(); await first.press('ArrowRight')
  await expect(root.getByRole('button', { name: 'G23 2028 Wheat', exact: true })).toBeFocused()
  const evidence = root.getByRole('region', { name: 'Evidence', exact: true })
  await expect(evidence.getByText('3 (2–5) t/ha · Scenario range', { exact: true })).toBeVisible()
  await expect(root.getByText('Worst tested margin: Unknown', { exact: true }).first()).toBeVisible()
  await expect(evidence.getByText('No guaranteed profit', { exact: true })).toBeVisible()
})

test('apply requires human confirmation and failed apply never reports success', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23&apply=stale')
  await root.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = root.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Confirm application', exact: true })).toBeDisabled()
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(root.getByRole('alert')).toContainText('Application failed: stale')
  await expect(root.getByText('Plan applied', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).not.toBeVisible()
})

test('successful apply is acknowledged and draft changes disable old result', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23')
  await root.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = root.getByRole('dialog')
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(root.getByText('Plan applied', { exact: true })).toBeVisible()
  await root.getByLabel('Budget', { exact: true }).first().fill('0')
  await expect(root.getByText('New inputs — calculate again', { exact: true })).toBeVisible()
  await expect(root.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeDisabled()
})

test('profile blanks preserve unknown values; save preserves field identity', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23')
  await root.locator('summary').filter({ hasText: /^Field profile$/ }).click()
  await expect(root.getByLabel('pH', { exact: true })).toHaveValue('')
  await root.getByLabel('pH', { exact: true }).fill('6.5')
  await root.getByRole('button', { name: 'Save profile', exact: true }).click()
  await expect(root.getByText('Profile saved', { exact: true })).toBeVisible()
  await expect(root.getByRole('heading', { name: 'Field profile · G23', exact: true })).toBeVisible()
  await expect(root.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeDisabled()
})

test('stale report and absent backend have explicit states', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23&stale=1&offline=1')
  await expect(root.getByText('Stale report — refresh inputs and replan', { exact: true })).toBeVisible()
  await expect(root.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeDisabled()
  await root.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(root.getByRole('alert')).toContainText('Backend unavailable — no replacement data')
})

for (const state of ['queued', 'running', 'failed', 'cancelled']) test(`job lifecycle: ${state}`, async ({ page }, testInfo) => {
  const root = await open(page, testInfo, `case=G23&state=${state}`)
  await expect(root.getByRole('status').filter({ hasText: new RegExp(`^${state[0].toUpperCase()}${state.slice(1)}$`) })).toBeVisible()
  await expect(root.getByRole('button', { name: 'Apply selected plan', exact: true })).toBeDisabled()
})

test('replan preserves previous report and shows assignment changes', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23')
  await root.getByRole('button', { name: 'Replan', exact: true }).click()
  await expect(root.getByText('Changes from previous plan', { exact: true })).toBeVisible()
  await expect(root.getByText('G23/2028: Wheat (—) → Soy (—)', { exact: true })).toBeVisible()
})

test('hemisphere/aspect and Ukrainian labels', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G15&locale=uk')
  await expect(root.getByRole('heading', { name: 'Планування сівозміни' })).toBeVisible()
  await expect(root.getByText('Широта: 45° · Півкуля: Північна', { exact: true })).toBeVisible()
  await root.locator('summary').filter({ hasText: /^Рельєф і сонце$/ }).click()
  await expect(root.getByText(/За годинниковою стрілкою від істинної півночі/)).toBeVisible()
})

test('HTTP writes carry existing session headers and never call external model services', async ({ page }, testInfo) => {
  let writeHeaders: Record<string, string> | undefined
  await page.route('**/api/planning-v1/**', async route => {
    const request = route.request()
    writeHeaders = request.headers()
    const body = request.postDataJSON()
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ schema_version: '1.0.0', request_id: body.request_id, status: 'applied', applied_at: '2026-11-14T12:00:00Z', reasons: [] }) })
  })
  const root = await open(page, testInfo, 'case=G23&http=1')
  await root.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = root.getByRole('dialog')
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(root.getByText('Plan applied', { exact: true })).toBeVisible()
  expect(writeHeaders?.['x-csrf-token']).toBe('synthetic-e2e-csrf')
  expect(writeHeaders?.['x-field-shift-request']).toBe('1')
})

test('HTTP stale rejection preserves server evidence and never acknowledges application', async ({ page }, testInfo) => {
  await page.route('**/api/planning-v1/**/apply', async route => {
    await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({
      schema_version: '1.0.0', request_id: route.request().postDataJSON().request_id,
      code: 'stale', retryable: false, reasons: [{ code: 'profile_revision_changed',
        message: 'The field profile changed after this report was calculated.',
        evidence: [{ id: 'profile-G23', kind: 'source', version: 'revision-2', locator: 'profile:profile-G23' }] }],
    }) })
  })
  const root = await open(page, testInfo, 'case=G23&http=1')
  await root.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = root.getByRole('dialog')
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(dialog).toHaveAccessibleName('Confirm application')
  await expect(dialog.getByRole('alert')).toContainText('Stale report — refresh inputs and replan')
  await expect(dialog.getByRole('alert')).toContainText('The field profile changed after this report was calculated.')
  await expect(dialog.getByRole('alert')).toContainText('revision-2')
  await expect(root.getByText('Plan applied', { exact: true })).toHaveCount(0)
})


test('new manual locks are not mislabeled as existing commitments during replan', async ({ page }, testInfo) => {
  const root = await open(page, testInfo, 'case=G23&strict=1')
  await root.locator('summary').filter({ hasText: /^Crop commitments$/ }).click()
  const commitment = root.getByRole('combobox', { name: 'G23 · 2027', exact: true })
  await commitment.selectOption({ index: 1 })
  await root.getByRole('button', { name: 'Replan', exact: true }).click()
  await expect(root.getByText('Changes from previous plan', { exact: true })).toBeVisible()
  await expect(root.getByRole('alert')).toHaveCount(0)
})

for (const refresh of ['delayed', 'fail-once']) test(`apply keeps replanning gated until commitments refresh: ${refresh}`, async ({ page }, testInfo) => {
  const root = await open(page, testInfo, `case=G23&strict=1&refresh=${refresh}`)
  await root.getByRole('button', { name: 'Apply selected plan', exact: true }).click()
  const dialog = root.getByRole('dialog')
  await dialog.getByLabel('I reviewed the plan and commitments', { exact: true }).check()
  await dialog.getByRole('button', { name: 'Confirm application', exact: true }).click()
  await expect(root.getByRole('button', { name: 'Replan', exact: true })).toBeDisabled()
  if (refresh === 'fail-once') {
    await expect(root.getByRole('alert')).toContainText('Controlled commitment refresh outage')
    await root.getByRole('button', { name: 'Retry commitment refresh', exact: true }).click()
  }
  await expect(root.getByRole('button', { name: 'Replan', exact: true })).toBeEnabled()
  await root.locator('summary').filter({ hasText: /^Crop commitments$/ }).click()
  await expect(root.getByRole('combobox', { name: 'G23 · 2027 · Locked', exact: true })).toBeDisabled()
  await root.getByRole('button', { name: 'Replan', exact: true }).click()
  await expect(root.getByText('Changes from previous plan', { exact: true })).toBeVisible()
  await expect(root.getByRole('alert')).toHaveCount(0)
})
