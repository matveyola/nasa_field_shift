import { useCallback, useEffect, useState } from 'react'

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, refreshMs?: number) {
  const [state, setState] = useState<{ data?: T; error?: string; pending: boolean }>({ pending: true })
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let loading = false
    async function fetchData(background = false) {
      if (loading) return
      loading = true
      try {
        const data = await load(controller.signal)
        if (!controller.signal.aborted) setState({ data, pending: false })
      } catch (error) {
        if (!controller.signal.aborted) setState((previous) => background && previous.data != null
          ? previous : { error: errorText(error), pending: false })
      } finally {
        loading = false
      }
    }
    function refresh() {
      if (!document.hidden) void fetchData(true)
    }

    setState({ pending: true })
    void fetchData()
    const timer = refreshMs === undefined ? undefined : window.setInterval(refresh, refreshMs)
    if (timer !== undefined) window.addEventListener('focus', refresh)
    return () => {
      controller.abort()
      if (timer !== undefined) {
        window.clearInterval(timer)
        window.removeEventListener('focus', refresh)
      }
    }
  }, [load, refreshMs, revision])

  const reload = useCallback(() => setRevision((current) => current + 1), [])
  const update = useCallback((apply: (data: T) => T) => {
    setState((current) => current.data === undefined ? current : { ...current, data: apply(current.data) })
  }, [])
  return { ...state, reload, update }
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}
