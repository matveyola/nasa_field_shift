import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { FieldContext, Job, JobKind, JobOptions } from '../api/contracts'
import { startJobPolling } from './jobPolling'
import { errorText } from './useResource'
import { useAccess } from '../features/access/AccessProvider'

export function useJob(jobId?: string) {
  const [job, setJob] = useState<Job>()
  const [error, setError] = useState<string>()
  useEffect(() => {
    setError(undefined)
    if (!jobId) return

    return startJobPolling(async (signal) => {
      try {
        const data = await api.job(jobId, signal)
        if (signal.aborted) return 'stop'
        setJob(data)
        setError(undefined)
        return data.status === 'succeeded' || data.status === 'failed' ? 'stop' : data.status
      } catch (failure) {
        if (signal.aborted) return 'stop'
        setError(errorText(failure))
        return failure instanceof ApiError && [400, 404, 422].includes(failure.status)
          ? 'stop' : 'retry'
      }
    }, document)
  }, [jobId])

  return { job: job?.id === jobId ? job : undefined, error }
}

export function useStartJob() {
  const { canEdit } = useAccess()
  const inFlight = useRef(false)
  const lastAttempt = useRef<{ signature: string; key: string } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string>()

  async function start(kind: JobKind, context: FieldContext, irrigation = 0, options: JobOptions = {}, revision?: number): Promise<Job | undefined> {
    if (!canEdit) { setError('Your role allows viewing only. Ask an administrator for edit access.'); return }
    if (inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    setError(undefined)
    const signature = JSON.stringify([kind, context, irrigation, revision, Object.entries(options).sort(([a], [b]) => a.localeCompare(b))])
    if (lastAttempt.current?.signature !== signature) {
      lastAttempt.current = { signature, key: crypto.randomUUID() }
    }

    try {
      const job = await api.createJob(kind, context, lastAttempt.current.key, irrigation, options, revision)
      lastAttempt.current = null
      return job
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return { start, submitting, error, allowed: canEdit }
}
