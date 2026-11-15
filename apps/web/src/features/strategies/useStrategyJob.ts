import { useEffect, useRef, useState } from 'react'
import type { Field } from '../../api/contracts'
import { rotationHandoffApi, rotationStrategyApi, type HandoffJob, type StrategySubmission } from '../../api/rotationHandoff'
import { errorText } from '../../shared/useResource'
import { activeJob, matchesJob } from './handoff/model'

export function useStrategyJob(field: Field) {
  const [job, setJob] = useState<HandoffJob>()
  const [error, setError] = useState<string>()
  const [submitting, setSubmitting] = useState(false)
  const [retryPoll, setRetryPoll] = useState(0)
  const requested = useRef<StrategySubmission | undefined>(undefined)
  const submitController = useRef<AbortController | undefined>(undefined)

  useEffect(() => () => submitController.current?.abort(), [])
  const jobId = job?.job_id
  const polling = activeJob(job)
  useEffect(() => {
    if (!polling || !jobId) return
    const controller = new AbortController()
    const submission = requested.current
    let timeout: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const response = await rotationHandoffApi.job(field.id, jobId!, controller.signal)
        if (controller.signal.aborted || requested.current !== submission) return
        if (!matchesJob(response, field.id, field.boundary_revision, jobId)) throw new Error('Field or boundary changed. Reload the field.')
        setJob(response)
        if (activeJob(response)) timeout = setTimeout(() => void poll(), 1500)
      } catch (failure) {
        if (!controller.signal.aborted && requested.current === submission) setError(errorText(failure))
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timeout) }
  }, [field.id, field.boundary_revision, jobId, polling, retryPoll])

  function clear() {
    submitController.current?.abort()
    requested.current = undefined
    setSubmitting(false); setJob(undefined); setError(undefined)
  }

  async function send(input: StrategySubmission) {
    if (submitController.current && !submitController.current.signal.aborted) return
    const controller = new AbortController()
    submitController.current = controller
    requested.current = input
    setSubmitting(true); setError(undefined); setJob(undefined)
    try {
      const response = await rotationStrategyApi.submit(field.id, input, controller.signal)
      if (controller.signal.aborted) return
      if (!matchesJob(response, field.id, field.boundary_revision)) throw new Error('Field or boundary changed. Reload the field.')
      setJob(response)
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorText(failure))
    } finally {
      if (submitController.current === controller) {
        submitController.current = undefined
        setSubmitting(false)
      }
    }
  }

  function retry() {
    if (job && polling) { setError(undefined); setRetryPoll(value => value + 1) }
    else if (requested.current) void send(requested.current)
  }

  return { job, error, busy: submitting || polling, retry, clear, send, plantingDate: requested.current?.planting_date }
}
