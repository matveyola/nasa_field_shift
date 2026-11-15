import { useCallback, useEffect, useRef, useState } from 'react'
import type { Field } from '../../../api/contracts'
import { rotationHandoffApi, type HandoffCatalog, type HandoffJob, type HandoffSubmission } from '../../../api/rotationHandoff'
import { errorText } from '../../../shared/useResource'
import { activeJob, forecastParameters, matchesJob, newDraft, newSubmission, type HandoffDraft } from './model'

type Session = { draft: HandoffDraft; submission?: HandoffSubmission; job?: HandoffJob; submitting: boolean; error?: string; pollError?: string; saved?: HandoffJob[]; savedError?: string }
export function useHandoff(field: Field, catalog?: HandoffCatalog) {
  const scope = JSON.stringify([field.id, field.boundary_revision])
  const [sessions, setSessions] = useState<Record<string, Session>>({})
  const fallback = useRef<Record<string, Session>>({})
  fallback.current[scope] ??= { draft: newDraft(field.irrigation_available), submitting: false }
  const session = sessions[scope] ?? fallback.current[scope]
  const [pollNonce, setPollNonce] = useState(0)
  const submitController = useRef<AbortController | undefined>(undefined)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; submitController.current?.abort() } }, [])
  useEffect(() => () => submitController.current?.abort(), [scope])
  const update = useCallback((apply: (current: Session) => Session) => {
    if (mounted.current) setSessions(all => ({ ...all, [scope]: apply(all[scope] ?? fallback.current[scope]) }))
  }, [scope])
  const [savedNonce, setSavedNonce] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void rotationHandoffApi.jobs(field.id, controller.signal).then(saved => {
      if (controller.signal.aborted) return
      if (saved.some(job => !matchesJob(job, field.id, field.boundary_revision))) throw new Error('scope_mismatch')
      update(current => ({ ...current, saved, savedError: undefined }))
    }).catch(error => { if (!controller.signal.aborted) update(current => ({ ...current, savedError: errorText(error) })) })
    return () => controller.abort()
  }, [field.id, field.boundary_revision, savedNonce, update])
  function openSaved(job: HandoffJob) {
    if (session.submitting || activeJob(session.job) || !matchesJob(job, field.id, field.boundary_revision)) return
    // Saved financial/request inputs are deliberately not reconstructed from result metrics.
    update(current => ({ ...current, submission: undefined, job, error: undefined, pollError: undefined }))
    setPollNonce(value => value + 1)
  }
  const jobId = session.job?.job_id
  const jobStatus = session.job?.status
  useEffect(() => {
    if (!jobId || !activeJob(session.job)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const job = await rotationHandoffApi.job(field.id, jobId!, controller.signal)
        if (controller.signal.aborted) return
        if (!matchesJob(job, field.id, field.boundary_revision, jobId)) throw new Error('scope_mismatch')
        update(current => ({ ...current, job, pollError: undefined }))
        if (activeJob(job)) timer = setTimeout(() => void poll(), 1500)
      } catch (error) {
        if (!controller.signal.aborted) update(current => ({ ...current, pollError: errorText(error) }))
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
    // Job identity/status, not every response object, controls this poll lifecycle.
  }, [field.id, field.boundary_revision, jobId, jobStatus, pollNonce, update])

  function edit(apply: (draft: HandoffDraft) => HandoffDraft) {
    if (session.submitting || activeJob(session.job)) return
    update(current => ({ draft: apply(current.draft), submitting: false, saved: current.saved, savedError: current.savedError }))
  }
  async function calculate(newRun = false) {
    if (!catalog || session.submitting || activeJob(session.job) || submitController.current && !submitController.current.signal.aborted) return
    let submission: HandoffSubmission
    try {
      submission = !newRun && session.submission ? session.submission : newSubmission(forecastParameters(session.draft, catalog, new Date().toISOString().slice(0, 10)), field.boundary_revision, session.draft.allowPublicData, crypto.randomUUID())
    } catch (error) { update(current => ({ ...current, error: errorText(error) })); return }
    const controller = new AbortController()
    submitController.current?.abort(); submitController.current = controller
    update(current => ({ ...current, submission, job: undefined, submitting: true, error: undefined, pollError: undefined }))
    try {
      const job = await rotationHandoffApi.submit(field.id, submission, controller.signal)
      if (controller.signal.aborted) throw new Error('submission_interrupted')
      if (!matchesJob(job, field.id, field.boundary_revision)) throw new Error('scope_mismatch')
      update(current => ({ ...current, job, submitting: false }))
    } catch (error) {
      update(current => ({ ...current, submitting: false, error: controller.signal.aborted ? 'submission_interrupted' : errorText(error) }))
    } finally { if (submitController.current === controller) submitController.current = undefined }
  }
  return { ...session, edit, calculate, openSaved, refreshSaved: () => setSavedNonce(value => value + 1), retryStatus: () => { update(current => ({ ...current, pollError: undefined })); setPollNonce(value => value + 1) }, busy: session.submitting || activeJob(session.job) }
}
