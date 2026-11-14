import assert from 'node:assert/strict'
import test from 'node:test'
import { startJobPolling } from '../src/shared/jobPolling.ts'

class PageVisibility extends EventTarget {
  hidden = false

  setHidden(hidden: boolean) {
    this.hidden = hidden
    this.dispatchEvent(new Event('visibilitychange'))
  }
}

test('job polling stays idle in the background and stops after a terminal response', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const page = new PageVisibility()
  page.setHidden(true)
  let requests = 0
  const stop = startJobPolling(async () => ++requests === 2 ? 'stop' : 'running', page)
  try {
    context.mock.timers.tick(60_000)
    assert.equal(requests, 0)

    page.setHidden(false)
    await Promise.resolve()
    assert.equal(requests, 1)

    page.setHidden(true)
    context.mock.timers.tick(60_000)
    assert.equal(requests, 1)

    page.setHidden(false)
    await Promise.resolve()
    context.mock.timers.tick(60_000)
    page.setHidden(true)
    page.setHidden(false)
    assert.equal(requests, 2)
  } finally {
    stop()
  }
})

test('long waits and transient failures back off independently with caps', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const page = new PageVisibility()
  let requests = 0
  let outcome: 'queued' | 'running' | 'retry' = 'queued'
  const stop = startJobPolling(async () => { requests++; return outcome }, page)

  async function expectNextRequest(delay: number) {
    const before = requests
    context.mock.timers.tick(delay - 1)
    assert.equal(requests, before)
    context.mock.timers.tick(1)
    await Promise.resolve()
    assert.equal(requests, before + 1)
  }

  try {
    await Promise.resolve()
    assert.equal(requests, 1)
    for (const delay of [1_500, 3_000, 6_000, 12_000]) await expectNextRequest(delay)
    outcome = 'retry'
    await expectNextRequest(12_000)
    for (const delay of [1_500, 3_000, 6_000, 12_000, 24_000, 30_000]) {
      await expectNextRequest(delay)
    }
    outcome = 'running'
    await expectNextRequest(30_000)
    await expectNextRequest(1_500)
  } finally {
    stop()
  }
})

test('visibility changes do not overlap a request and cleanup aborts it', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const page = new PageVisibility()
  let requests = 0
  let signal: AbortSignal | undefined
  let resolve!: (outcome: 'running') => void
  const pending = new Promise<'running'>((complete) => { resolve = complete })
  const stop = startJobPolling((requestSignal) => {
    requests++
    signal = requestSignal
    return pending
  }, page)

  page.setHidden(true)
  page.setHidden(false)
  assert.equal(requests, 1)
  stop()
  assert.equal(signal?.aborted, true)

  resolve('running')
  await Promise.resolve()
  context.mock.timers.tick(60_000)
  page.setHidden(true)
  page.setHidden(false)
  assert.equal(requests, 1)
})
