import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { analyticsApi, type VegetationObservation, type VegetationResult } from '../../api/analytics'
import type { Field } from '../../api/contracts'
import { errorText, useResource } from '../../shared/useResource'
import { useJob } from '../../shared/jobs'
import { usableObservations } from './vegetationInsights'

function useVegetationScene(result: VegetationResult | undefined, observation: VegetationObservation | undefined) {
  const fieldId = result?.field_id
  const season = result?.season
  const revision = result?.boundary_revision
  const fingerprint = result?.geometry_fingerprint
  const generatedAt = result?.generated_at
  const date = observation?.date
  const sceneId = observation?.scene_id
  const requestKey = fieldId && season && date && sceneId
    ? JSON.stringify([fieldId, season, revision, fingerprint, generatedAt, date, sceneId]) : undefined
  const load = useCallback(async (signal: AbortSignal) => {
    if (!requestKey || !fieldId || !season || !date || !sceneId) return undefined
    const data = await analyticsApi.vegetationScene(fieldId, season, date, sceneId, signal)
    return { requestKey, data: data && data.field_id === fieldId && data.season === season
      && data.boundary_revision === revision && data.geometry_fingerprint === fingerprint
      && data.observation.date === date && data.observation.scene_id === sceneId && data.heterogeneity.date === date
      ? data : null }
  }, [requestKey, fieldId, season, date, sceneId, revision, fingerprint])
  const resource = useResource(load)
  const current = resource.data?.requestKey === requestKey ? resource.data?.data : undefined
  return { ...resource, data: current,
    pending: Boolean(requestKey && current === undefined && !resource.error) }
}

export function useFieldAnalytics(field: Field | undefined, season: number, includeMap = true) {
  const fieldId = field?.id
  const revision = field?.boundary_revision
  const footprint = JSON.stringify(field?.planting?.geometry ?? field?.geometry)
  const loadVegetation = useCallback((signal: AbortSignal) => fieldId
    ? analyticsApi.vegetation(fieldId, season, signal, false).then((data) => ({ ...data, requestFootprint: footprint }))
    : Promise.resolve(undefined), [fieldId, season, revision, footprint])
  const loadTerrain = useCallback((signal: AbortSignal) => fieldId && includeMap
    ? analyticsApi.terrain(fieldId, signal) : Promise.resolve(undefined), [fieldId, revision, includeMap])
  const vegetation = useResource(loadVegetation, 30_000)
  const current = vegetation.data && vegetation.data.field_id === fieldId && vegetation.data.boundary_revision === revision
    && vegetation.data.season === season && vegetation.data.requestFootprint === footprint ? vegetation.data : undefined
  const observations = useMemo(() => usableObservations(current), [current])
  const scope = JSON.stringify([fieldId, season, revision, footprint])
  const [selection, setSelection] = useState<{ scope: string; primaryDate?: string; comparisonDate?: string; showComparison?: boolean; index?: import('../../api/analytics').SpectralIndex }>()
  const scoped = selection?.scope === scope ? selection : undefined
  const primary = observations.find((point) => point.date === scoped?.primaryDate) ?? observations.at(-1)
  const alternatives = observations.filter((point) => point.date !== primary?.date)
  const comparison = alternatives.find((point) => point.date === scoped?.comparisonDate)
  const selectedScene = useVegetationScene(current, includeMap ? primary : undefined)
  const comparisonScene = useVegetationScene(current, includeMap ? comparison : undefined)
  const showingComparison = Boolean(comparison && scoped?.showComparison)
  const spectralIndex = scoped?.index
  const showingMsavi = spectralIndex === 'msavi'

  function selectObservation(date: string) {
    if (!observations.some((point) => point.date === date)) return
    setSelection({ ...scoped, scope, primaryDate: date, showComparison: false,
      comparisonDate: date === comparison?.date ? primary?.date : comparison?.date })
  }

  function selectComparison(date?: string) {
    setSelection({ ...scoped, scope, primaryDate: primary?.date, comparisonDate: date,
      showComparison: Boolean(date), index: undefined })
  }

  function showComparison(show: boolean) {
    setSelection({ ...scoped, scope, showComparison: show, index: undefined })
  }

  function showMsavi(show: boolean) {
    setSelection({ ...scoped, scope, showComparison: false, index: show ? 'msavi' : undefined })
  }
  function showIndex(index: import('../../api/analytics').SpectralIndex) {
    setSelection({ ...scoped, scope, showComparison: false, index })
  }
  const terrain = useResource(loadTerrain)
  const [submitting, setSubmitting] = useState<'vegetation' | 'terrain'>()
  const [error, setError] = useState<string>()
  const [terrainJobId, setTerrainJobId] = useState<string>()
  const { job: terrainJob, error: terrainJobError } = useJob(terrainJobId)
  const request = useRef<AbortController | null>(null)
  const busy = useRef(false)
  useEffect(() => {
    setSubmitting(undefined)
    setError(undefined)
    setTerrainJobId(undefined)
    busy.current = false
    return () => { request.current?.abort() }
  }, [fieldId, season, revision, footprint])
  useEffect(() => {
    if (terrainJob?.status === 'succeeded') terrain.reload()
  }, [terrainJob?.status, terrain.reload])

  async function refresh(kind: 'vegetation' | 'terrain') {
    if (!fieldId || busy.current) return
    busy.current = true
    const controller = new AbortController()
    request.current = controller
    setSubmitting(kind)
    setError(undefined)
    try {
      if (kind === 'vegetation') {
        await analyticsApi.refreshVegetation(fieldId, season, controller.signal)
        if (!controller.signal.aborted) vegetation.reload()
      } else {
        const job = await analyticsApi.refreshTerrain(fieldId, controller.signal)
        if (!controller.signal.aborted) setTerrainJobId(job.id)
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure))
    } finally {
      if (!controller.signal.aborted) { setSubmitting(undefined); busy.current = false }
    }
  }
  return {
    vegetation: { ...vegetation, data: current },
    observations, primary, comparison, alternatives, showingComparison, showingMsavi, spectralIndex, showIndex,
    selectedScene, comparisonScene, activeScene: showingComparison ? comparisonScene : selectedScene,
    selectObservation, selectComparison, showComparison, showMsavi,
    terrain: { ...terrain, data: terrain.data && terrain.data.field_id === fieldId && terrain.data.boundary_revision === revision ? terrain.data : undefined },
    refresh, submitting, error: error ?? terrainJobError ?? (terrainJob?.status === 'failed' ? terrainJob.error ?? 'Terrain analysis failed.' : undefined),
    terrainPending: Boolean(terrainJob && !['succeeded', 'failed'].includes(terrainJob.status)),
  }
}
