import type { JobStatus } from '../api/contracts'

const INITIAL_DELAY_MS = 1_500
const MAX_PENDING_DELAY_MS = 12_000
const MAX_RETRY_DELAY_MS = 30_000

type PollOutcome = Exclude<JobStatus, 'succeeded' | 'failed'> | 'retry' | 'stop'

export function startJobPolling(
  poll: (signal: AbortSignal) => Promise<PollOutcome>,
  visibility: EventTarget & { readonly hidden: boolean },
): () => void {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight = false
  let previous: PollOutcome | undefined
  let delay = INITIAL_DELAY_MS

  function stop() {
    controller.abort()
    clearTimeout(timer)
    visibility.removeEventListener('visibilitychange', onVisibilityChange)
  }

  async function run() {
    if (visibility.hidden || inFlight || controller.signal.aborted) return
    inFlight = true
    const outcome = await poll(controller.signal)
    inFlight = false
    if (controller.signal.aborted) return
    if (outcome === 'stop') {
      stop()
      return
    }

    const maxDelay = outcome === 'retry' ? MAX_RETRY_DELAY_MS : MAX_PENDING_DELAY_MS
    delay = outcome === previous ? Math.min(delay * 2, maxDelay) : INITIAL_DELAY_MS
    previous = outcome
    if (!visibility.hidden) timer = setTimeout(() => { void run() }, delay)
  }

  function onVisibilityChange() {
    clearTimeout(timer)
    if (!visibility.hidden) void run()
  }

  visibility.addEventListener('visibilitychange', onVisibilityChange)
  void run()
  return stop
}
