// The farmer's pending choice about replacing an earlier planning decision, kept apart from React so the
// assistant's exact flow and its cancellation rules can be tested on their own. At most one question is open. It
// belongs to the field context (field, boundary, season, scenario, run) and the open assistant it was asked in:
// closing the assistant, another field context, a new question or unmounting answers it "keep" (nothing is
// replaced), and an answered question is never shown again.

export interface ReplacementReview<T> {
  /** The view the assistant shows now (Assistant.tsx calls this whenever it changes). */
  sync(key: string | undefined, open: boolean): void
  /** Asks the farmer; resolves true for "Replace", false for "Keep" or any cancellation. A question for a view
   *  that is not the open one now (the request returned after the farmer closed or moved on) is "keep" at once. */
  ask(key: string, items: readonly T[]): Promise<boolean>
  /** The farmer's answer to the open question (no-op when none is open). */
  answer(replace: boolean): void
  /** Answers "keep": the view unmounted. */
  cancel(): void
  /** What to show for this render: the open question for this context while the assistant is open, else nothing. */
  shown(key: string | undefined, open: boolean): readonly T[]
  subscribe(listener: () => void): () => void
}

export function createReplacementReview<T>(): ReplacementReview<T> {
  let view: { key: string | undefined; open: boolean } = { key: undefined, open: false }
  let pending: { key: string; items: readonly T[]; resolve: (replace: boolean) => void } | undefined
  const listeners = new Set<() => void>()
  const notify = () => listeners.forEach((listener) => listener())

  function settle(replace: boolean) {
    const open = pending
    if (!open) return
    pending = undefined
    notify()
    open.resolve(replace)
  }

  return {
    sync(key, open) {
      view = { key, open }
      if (pending && (!open || pending.key !== key)) settle(false)
    },
    ask(key, items) {
      settle(false) // one question at a time: an older one is kept, not replaced
      if (!view.open || view.key !== key) return Promise.resolve(false)
      return new Promise((resolve) => {
        pending = { key, items, resolve }
        notify()
      })
    },
    answer: settle,
    cancel: () => settle(false),
    shown: (key, open) => (open && pending && pending.key === key ? pending.items : []),
    subscribe(listener) {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
  }
}
