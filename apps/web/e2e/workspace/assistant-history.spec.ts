/** Explicit synthetic persisted-response fixtures test history UI; no live conversation is fabricated. */
import { test, expect, type Page } from '@playwright/test'

const geometry = { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]] }
const fields = ['F-01', 'F-02'].map(id => ({ id, name: 'History test ' + id, crop: 'Maize', area_ha: 1, geometry, boundary_revision: 2, planting: null }))
const first = { id: 'saved-1', field_id: 'F-01', season: 2024, scenario: 'less_rain', title: 'Earlier irrigation question', updated_at: '2024-08-02T12:30:00Z' }
const second = { ...first, id: 'saved-2', title: 'Earlier crop question' }
const message = { id: 2, role: 'assistant', text: 'Recorded answer <script>alert(1)</script>', created_at: first.updated_at, demo: false, provider: 'openai', actions: [{ label: 'Apply old instruction', target: 'fields' }], options: ['Send old reply'], citations: [
  { id: 'source', title: 'Recorded FAO source', url: 'https://www.fao.org/example', publisher: 'FAO', year: 2024 },
  { id: 'unsafe', title: 'Unsafe source kept as text', url: 'javascript:alert(1)' },
  { id: 'credentials', title: 'Credential URL kept as text', url: 'https://name:secret@example.com' },
] }
async function fixture(page: Page, options: { empty?: boolean; errorOnce?: boolean; mismatch?: 'field' | 'id' | 'year' | 'scenario'; delayed?: boolean; language?: 'uk'; listMismatch?: boolean } = {}) {
  let unavailable = options.errorOnce ?? false
  let release: (() => void) | undefined
  const methods: string[] = []
  await page.addInitScript(language => { sessionStorage.setItem('field-shift-desktop', '1'); if (language) localStorage.setItem('field-shift-language', language) }, options.language)
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    methods.push(route.request().method())
    let json: unknown
    if (path === '/api/auth/session') json = { mode: 'local', authenticated: true, needs_setup: false, setup_available: false, user: null, role: 'admin', csrf_token: null }
    else if (path === '/api/fields') json = fields
    else if (path === '/api/farm') json = { name: 'Synthetic history farm', season: 2026, total_area_ha: 2, defaults: { irrigation_available: false, priority: '', equipment: null, budget: null }, observations: [] }
    else if (path === '/api/agent/conversations') {
      if (unavailable) return route.fulfill({ status: 503, json: { detail: 'Synthetic unavailable history' } })
      json = options.listMismatch ? [{ ...first, field_id: 'F-02' }] : options.empty || url.searchParams.get('field_id') !== first.field_id ? [] : [first, second]
    } else if (path.startsWith('/api/agent/conversations/')) {
      const row = path.endsWith('/saved-2') ? second : first
      if (options.delayed && row.id === first.id) await new Promise<void>(resolve => { release = resolve })
      json = { ...row, messages: [
        { id: 1, role: 'user', text: 'What did I ask then?', created_at: first.updated_at, demo: false, actions: [] },
        { ...message, text: row.id === second.id ? 'Second saved answer' : message.text },
        { ...message, id: 3, demo: true, text: 'Stored example, not a measurement', citations: [] },
      ], ...(options.mismatch === 'field' ? { field_id: 'F-02' } : options.mismatch === 'id' ? { id: 'wrong' } : options.mismatch === 'year' ? { season: 2025 } : options.mismatch === 'scenario' ? { scenario: 'baseline' } : {}) }
    } else if (path.endsWith('/field-context')) json = { schema_version: '0.1', field_id: path.includes('/F-02/') ? 'F-02' : 'F-01', revision: 2, geometry, centroid: [30, 50], area_ha: 1, region: { country_iso2: 'ZZ' }, locale: options.language ?? 'en', facts: [], timeseries_index: [], available_layers: [], available_charts: [] }
    else if (path.endsWith('/health/ready')) return route.fulfill({ status: 503, json: { status: 'not_configured' } })
    else return route.fulfill({ status: 404, json: { detail: 'Not supplied by synthetic UI fixture' } })
    await route.fulfill({ json }).catch(() => {}) // a cancelled fetch may already be gone
  })
  await page.goto('/agent?field=F-01&season=2026&scenario=baseline&desktop=1')
  return { methods, recover: () => { unavailable = false }, release: () => release?.(), pending: () => !!release }
}
async function openHistory(page: Page) {
  await page.getByRole('button', { name: 'Saved conversations', exact: true }).click()
  return page.getByRole('dialog', { name: 'Saved conversations', exact: true })
}

test('saved records are read-only, scoped and safe; Escape restores the current draft and history button', async ({ page }) => {
  const state = await fixture(page)
  const draft = page.getByRole('textbox', { name: 'Message to the assistant' })
  await expect(draft).toBeEnabled()
  await draft.fill('Keep my current unsent question')
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect(dialog.getByText(message.text, { exact: true })).toBeVisible()
  await expect(dialog.getByText(/Analysis year: 2024 · Scenario:/)).toBeVisible()
  await expect(dialog.getByText('Example response', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Recorded FAO source' })).toHaveAttribute('href', 'https://www.fao.org/example')
  await expect(dialog.getByRole('link', { name: /Unsafe|Credential/ })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: /Apply old|Send old/ })).toHaveCount(0)
  await expect(dialog.getByRole('textbox')).toHaveCount(0)
  expect(state.methods.every(method => method === 'GET')).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Saved conversations', exact: true })).toBeFocused()
  await expect(draft).toHaveValue('Keep my current unsent question')
  await expect(page.getByRole('dialog', { name: 'Field Assistant', exact: true })).toBeVisible()
  await page.goto('/agent?field=F-01&season=2026&scenario=baseline&desktop=1')
  const reopened = await openHistory(page)
  await expect(reopened.getByRole('button', { name: /Earlier irrigation question/ })).toBeVisible()
})

test('history failure can retry to the truthful empty state', async ({ page }) => {
  const state = await fixture(page, { empty: true, errorOnce: true })
  const dialog = await openHistory(page)
  await expect(dialog.getByRole('alert')).toContainText('Saved conversations could not be loaded.')
  state.recover()
  await dialog.getByRole('button', { name: 'Try again' }).click()
  await expect(dialog.getByText('No saved conversations for this field.')).toBeVisible()
  await expect(dialog.getByText(/New chats in the current assistant are not saved/)).toBeVisible()
})

for (const mismatch of ['field', 'id', 'year', 'scenario'] as const) test(`history refuses a mismatched ${mismatch} response`, async ({ page }) => {
  await fixture(page, { mismatch })
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect(dialog.getByRole('alert')).toContainText('does not match')
  await expect(dialog.getByText(message.text, { exact: true })).toHaveCount(0)
})

test('a late detail cannot replace another selected conversation', async ({ page }) => {
  const state = await fixture(page, { delayed: true })
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect.poll(state.pending).toBe(true)
  await dialog.getByRole('button', { name: 'All saved conversations' }).click()
  await dialog.getByRole('button', { name: /Earlier crop question/ }).click()
  await expect(dialog.getByText('Second saved answer')).toBeVisible()
  state.release()
  await expect(dialog.getByText(message.text, { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('Second saved answer')).toBeVisible()
})

test('field navigation closes history and fences a delayed old-field response', async ({ page }) => {
  const state = await fixture(page, { delayed: true })
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect.poll(state.pending).toBe(true)
  await page.evaluate(() => { history.pushState({}, '', '/fields?field=F-02&season=2026&scenario=baseline&desktop=1'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(dialog).not.toBeVisible()
  state.release()
  const next = await openHistory(page)
  await expect(next.getByText('History test F-02', { exact: true })).toBeVisible()
  await expect(next.getByText('No saved conversations for this field.')).toBeVisible()
  await expect(next.getByText(message.text, { exact: true })).toHaveCount(0)
})

test('a list containing a different field is rejected before any conversation can open', async ({ page }) => {
  await fixture(page, { listMismatch: true })
  const dialog = await openHistory(page)
  await expect(dialog.getByRole('alert')).toContainText('does not match')
  await expect(dialog.getByRole('button', { name: /Earlier irrigation question/ })).toHaveCount(0)
})

test('closing history cancels pending detail and reopening does not display the late answer', async ({ page }) => {
  const state = await fixture(page, { delayed: true })
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect.poll(state.pending).toBe(true)
  await dialog.getByRole('button', { name: 'Close saved conversations' }).click()
  await expect(page.getByRole('button', { name: 'Saved conversations', exact: true })).toBeFocused()
  state.release()
  const reopened = await openHistory(page)
  await expect(reopened.getByRole('button', { name: /Earlier irrigation question/ })).toBeVisible()
  await expect(reopened.getByText(message.text, { exact: true })).toHaveCount(0)
})

test('Ukrainian history fits320px and traps keyboard focus without closing the underlying assistant', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 })
  await fixture(page, { language: 'uk' })
  const button = page.getByRole('button', { name: 'Збережені розмови', exact: true })
  const box = await button.boundingBox()
  expect(box!.width).toBeGreaterThanOrEqual(44)
  expect(box!.height).toBeGreaterThanOrEqual(44)
  await button.click()
  const dialog = page.getByRole('dialog', { name: 'Збережені розмови', exact: true })
  await dialog.getByRole('button', { name: /Earlier irrigation question/ }).click()
  await expect(dialog.getByText('Приклад відповіді')).toBeVisible()
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press(i < 5 ? 'Tab' : 'Shift+Tab')
    expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(button).toBeFocused()
  await expect(page.getByRole('textbox', { name: 'Повідомлення помічнику' })).toBeVisible()
})
