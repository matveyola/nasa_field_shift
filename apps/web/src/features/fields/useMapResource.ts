import { useCallback, useEffect, useState } from 'react'
import { errorText } from '../../shared/useResource'
import { mapRequestResult } from './fieldMapVisualization'

type Request<T> = (signal: AbortSignal) => Promise<T>
type Resource<T> = { data?: T; error?: string; pending: boolean }

export function useMapResource<T>(load: Request<T>, refreshMs?: number) {
  const [revision, setRevision] = useState(0)
  const [response, setResponse] = useState<{ request: Request<T>; revision: number; value: Resource<T> }>()

  useEffect(() => {
    const controller = new AbortController()
    let loading = false
    async function fetchData(background = false) {
      if (loading) return
      loading = true
      try {
        const data = await load(controller.signal)
        if (!controller.signal.aborted) setResponse({ request: load, revision, value: { data, pending: false } })
      } catch (error) {
        if (!controller.signal.aborted) setResponse((previous) =>
          background && previous?.request === load && previous.revision === revision && previous.value.data != null
            ? previous : { request: load, revision, value: { error: errorText(error), pending: false } })
      } finally {
        loading = false
      }
    }
    function refresh() {
      if (!document.hidden) void fetchData(true)
    }

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
  const state = mapRequestResult(load, response?.revision === revision ? response : undefined)
  return { ...(state ?? { pending: true }), reload } as Resource<T> & { reload: () => void }
}
