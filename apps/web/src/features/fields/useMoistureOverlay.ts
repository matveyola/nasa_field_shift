import { useCallback, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { MoistureSource } from '../../api/contracts'
import { useJob, useStartJob } from '../../shared/jobs'
import { useResource } from '../../shared/useResource'
import { isStaleMoisture, latestMoistureArchiveDate, visibleMoisture } from './moisture'

export function useMoistureOverlay(source: MoistureSource, fieldId: string | undefined, season: number, boundaryRevision: number) {
  const canRefresh = source !== 'demo'
  const [sampleDate, setSampleDate] = useState('')
  const [jobIds, setJobIds] = useState<Record<string, string>>({})
  const querySeason = canRefresh ? (sampleDate ? Number(sampleDate.slice(0, 4)) : undefined) : season
  const key = fieldId && canRefresh ? fieldId + ':' + boundaryRevision + ':' + sampleDate : undefined
  const load = useCallback(async (signal: AbortSignal) => fieldId
    ? { revision: boundaryRevision, layer: await api.moisture(fieldId, source, querySeason, sampleDate, signal) }
    : undefined, [fieldId, source, querySeason, sampleDate, boundaryRevision])
  const resource = useResource(load, canRefresh && fieldId && !sampleDate ? 5 * 60 * 1000 : undefined)
  const jobId = key ? jobIds[key] : undefined
  const { job, error: jobError } = useJob(jobId)
  const launch = useStartJob()
  const cached = resource.data?.revision === boundaryRevision ? resource.data.layer : undefined
  const currentJob = (job?.boundary_revision ?? 0) === boundaryRevision ? job : undefined
  const layer = fieldId ? visibleMoisture(cached, currentJob, fieldId, querySeason, source, sampleDate) : undefined
  const pending = Boolean(jobId && !jobError && job?.status !== 'succeeded' && job?.status !== 'failed')
  const archiveDate = latestMoistureArchiveDate(new Date(), source)

  async function refresh(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!fieldId || !key || !canRefresh) return
    const archiveSeason = Number(archiveDate.slice(0, 4))
    const runSeason = querySeason ?? (source === 'ml' ? layer?.season ?? archiveSeason : archiveSeason)
    const next = await launch.start('geo_ml', { field_id: fieldId, season: runSeason, scenario: 'baseline' }, 0, {
      source, ...(sampleDate ? { sample_date: sampleDate } : source === 'historical' ? { latest: true } : {}),
    })
    if (next) setJobIds((current) => ({ ...current, [key]: next.id }))
  }

  return {
    source, layer, resource, canRefresh, pending, sampleDate, setSampleDate, archiveDate, refresh,
    stale: Boolean(layer && canRefresh && !sampleDate && isStaleMoisture(layer.date)),
    submitting: launch.submitting, submitError: launch.error, jobId, job, jobError,
  }
}

export type MoistureOverlay = ReturnType<typeof useMoistureOverlay>
