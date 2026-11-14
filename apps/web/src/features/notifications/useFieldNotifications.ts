import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { analyticsApi } from '../../api/analytics'
import type { Field } from '../../api/contracts'
import { loadFieldNotifications } from './fieldNotifications'

const refreshInterval = 5 * 60_000
const focusCooldown = 60_000
type Snapshot = Awaited<ReturnType<typeof loadFieldNotifications>> & { day: string; scope: string }

/** One farm-wide reader; no inference jobs, geometry cells or external provider calls. */
export function useFieldNotifications(fields: Field[], season: number) {
  const scope = JSON.stringify([season, fields.map((field) =>
    [field.id, field.name, field.boundary_revision, field.planting?.geometry ?? field.geometry])])
  const inputs = useRef(fields)
  inputs.current = fields
  const [state, setState] = useState<{ data?: Snapshot; pending: boolean; failed: boolean }>({ pending: true, failed: false })
  const refreshRef = useRef<() => void>(() => {})
  const reload = useCallback(() => refreshRef.current(), [])

  useEffect(() => {
    const controller = new AbortController()
    const snapshotFields = inputs.current
    let loading = false
    let lastAttempt = 0
    async function refresh(force = false) {
      if (loading || controller.signal.aborted || document.hidden || (!force && Date.now() - lastAttempt < focusCooldown)) return
      loading = true
      lastAttempt = Date.now()
      setState((previous) => ({ ...previous, pending: true, failed: false }))
      const day = new Date().toISOString().slice(0, 10)
      try {
        const data = await loadFieldNotifications(snapshotFields, season, day, controller.signal, {
          waterForecast: (id, signal) => api.waterForecast(id, AbortSignal.any([signal, AbortSignal.timeout(15_000)])),
          vegetation: (id, year, signal) => analyticsApi.vegetation(id, year, AbortSignal.any([signal, AbortSignal.timeout(15_000)]), false),
        })
        if (!controller.signal.aborted) setState({ data: { ...data, day, scope }, pending: false, failed: false })
      } catch {
        if (!controller.signal.aborted) setState({ pending: false, failed: true })
      } finally { loading = false }
    }
    setState({ pending: true, failed: false })
    refreshRef.current = () => { void refresh(true) }
    const onVisible = () => { if (!document.hidden) void refresh() }
    void refresh(true)
    const timer = window.setInterval(onVisible, refreshInterval)
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      controller.abort()
      window.clearInterval(timer)
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
      refreshRef.current = () => {}
    }
  }, [scope, season])

  // Discard previous footprints immediately, including the render before effect cleanup.
  const data = state.data?.scope === scope && state.data.day === new Date().toISOString().slice(0, 10) ? state.data : undefined
  return { data, pending: state.pending, failed: state.failed, reload }
}
