import { useEffect, useId, useRef, useState } from 'react'
import type { CropRef, FarmPlanningRequest, FieldProfileSnapshot, PlanAlternative, PlanningRun, Reason } from '../../api/planningV1.generated'
import { createPlanningApi, PlanningApiFailure, type PlanningApi } from '../../api/planningV1'
import { FieldProfileEditor } from '../field-profile/FieldProfileEditor'
import { profileWrite } from '../field-profile/model'
import { FieldDataCards, Reasons } from './EvidenceViews'
import { PlanComparison } from './PlanComparison'
import { PlanSettings } from './PlanSettings'
import { Select } from './controls'
import { decode } from './contracts'
import { applyRequest, clone, distinctPlans, prepareRequest, runLabel, staleRun, type PlanningContext, type PlanningEvidence } from './model'
import { tr, type PlanningLocale } from './i18n'
import './planning.css'

export interface PlanningWorkspaceProps {
  embedded?: boolean
  fieldNames?: Record<string, string>
  focus?: 'profiles' | 'economics'
  initialRequest: FarmPlanningRequest
  initialRun?: PlanningRun
  /** Latest independently fetched snapshots. A revision change disables applying old results. */
  currentProfiles?: FieldProfileSnapshot[]
  cropOptions: CropRef[]
  context: PlanningContext
  onContextChange: (context: PlanningContext) => void
  locale?: PlanningLocale
  api?: PlanningApi
  /** Integration #7 resolves existing candidate/evaluation IDs; no invented HTTP endpoint. */
  loadEvidence?: (run: PlanningRun, signal: AbortSignal) => Promise<PlanningEvidence>
  onProfileSaved?: (profile: FieldProfileSnapshot) => void
  onApplied?: (signal: AbortSignal) => Promise<FarmPlanningRequest>
  clock?: () => Date
}
const defaultClock = () => new Date()
const defaultApi = createPlanningApi()
const emptyEvidence: PlanningEvidence = { candidates: [], evaluations: [] }

export function PlanningWorkspace(props: PlanningWorkspaceProps) {
  return <WorkspaceSession key={props.initialRequest.farm_id} {...props} />
}

function WorkspaceSession({ initialRequest, initialRun, currentProfiles, cropOptions, context, onContextChange,
  embedded = false, fieldNames = {}, focus, locale = 'uk', api = defaultApi, loadEvidence, onProfileSaved, onApplied, clock = defaultClock }: PlanningWorkspaceProps) {
  const Container = embedded ? 'section' : 'main'
  const t = tr(locale)
  const fieldName = (id: string) => fieldNames[id] ?? id
  const [draft, setDraft] = useState<FarmPlanningRequest>(() => clone(initialRequest))
  const [profiles, setProfiles] = useState(() => clone(initialRequest.fields))
  const [selectedFields, setSelectedFields] = useState(() => initialRequest.fields.map(f => f.field_id))
  const [run, setRun] = useState<PlanningRun | null>(initialRun ?? null)
  const [evidence, setEvidence] = useState<PlanningEvidence>(emptyEvidence)
  const [alternativeId, setAlternativeId] = useState(initialRun?.comparison?.alternatives[0]?.alternative_id ?? '')
  const [previous, setPrevious] = useState<PlanAlternative>()
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [needsCommitments, setNeedsCommitments] = useState(false)
  const [confirmedLocks, setConfirmedLocks] = useState(() => clone(initialRequest.locks))
  const [error, setError] = useState('')
  const [errorReasons, setErrorReasons] = useState<Reason[]>([])
  const [notice, setNotice] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [checked, setChecked] = useState(false)
  const [savedRuns, setSavedRuns] = useState<PlanningRun[]>([])
  const [tick, setTick] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)
  const dialogTitle = useId()
  const mutation = useRef<AbortController | null>(null)
  const pendingSubmit = useRef<{ fingerprint: string; request: FarmPlanningRequest } | null>(null)
  const pendingApply = useRef<{ key: string; id: string } | null>(null)
  const mounted = useRef(true)
  const polls = useRef(0)
  const knownLocks = useRef(JSON.stringify(initialRequest.locks))
  useEffect(() => {
    const next = JSON.stringify(initialRequest.locks)
    if (next === knownLocks.current) return
    knownLocks.current = next
    // Server-confirmed commitments survive apply → replan in the same workspace.
    setConfirmedLocks(clone(initialRequest.locks))
    setDraft(old => ({ ...old, locks: clone(initialRequest.locks) }))
    setDirty(true); setConfirm(false)
  }, [initialRequest.locks])
  const active = profiles.find(f => f.field_id === context.fieldId) ?? profiles[0]
  const plans = distinctPlans(run?.comparison?.alternatives ?? [])
  const selected = plans.find(p => p.alternative_id === alternativeId) ?? plans[0]
  const latestProfiles = currentProfiles ?? profiles
  const stale = !!run && staleRun(run, latestProfiles, initialRequest.weather, clock())
  const synthetic = initialRequest.weather.scenario_type === 'synthetic' || profiles.some(p => p.sources.some(s => s.kind === 'synthetic'))

  useEffect(() => {
    mounted.current = true
    const timer = setInterval(() => setTick(n => n + 1), 30000)
    return () => { mounted.current = false; mutation.current?.abort(); clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (confirm) { setChecked(false); dialog.current?.showModal() }
    else dialog.current?.close()
  }, [confirm])
  useEffect(() => {
    setEvidence(emptyEvidence)
    if (!run || !loadEvidence) return
    const abort = new AbortController()
    void loadEvidence(run, abort.signal).then(result => {
      if (abort.signal.aborted) return
      setEvidence({ candidates: result.candidates.map(value => decode('candidate', value)), evaluations: result.evaluations.map(value => decode('evaluation', value)) })
    }).catch(() => { if (!abort.signal.aborted) setError('Missing model evidence') })
    return () => abort.abort()
  }, [run, loadEvidence])
  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status) || polls.current >= 60) return
    const abort = new AbortController()
    const timer = setTimeout(() => {
      if (document.visibilityState === 'hidden') { setTick(n => n + 1); return }
      polls.current += 1
      void api.run(run.request.farm_id, run.run_id, abort.signal).then(next => {
        if (!abort.signal.aborted) { setRun(next); setAlternativeId(next.comparison?.alternatives[0]?.alternative_id ?? '') }
      }).catch(() => { if (!abort.signal.aborted) setError('Backend unavailable — no replacement data') })
    }, 2500)
    return () => { abort.abort(); clearTimeout(timer) }
  }, [run, api, tick])

  async function operation(action: (signal: AbortSignal) => Promise<void>) {
    if (mutation.current) return
    const abort = new AbortController()
    mutation.current = abort
    setBusy(true); setError(''); setErrorReasons([]); setNotice('')
    try { await action(abort.signal) }
    catch (cause) { if (!abort.signal.aborted && mounted.current) {
      setError(cause instanceof Error ? cause.message : 'Backend unavailable — no replacement data')
      if (cause instanceof PlanningApiFailure) setErrorReasons(cause.reasons)
    } }
    finally { if (mounted.current) { mutation.current = null; setBusy(false) } }
  }
  async function submit(signal: AbortSignal) {
    const fingerprint = JSON.stringify([draft, selectedFields, run?.run_id])
    if (pendingSubmit.current?.fingerprint !== fingerprint) pendingSubmit.current = { fingerprint, request: prepareRequest(draft, selectedFields, crypto.randomUUID(), clock()) }
    const request = pendingSubmit.current.request
    const next = run ? await api.replan({ schema_version: '1.0.0', previous_run_id: run.run_id, request,
      preserve_commitment_ids: [...new Set([...initialRequest.locks, ...confirmedLocks, ...run.request.locks].map(l => l.commitment_id!).filter(Boolean))] }, signal) : await api.submit(request, signal)
    if (signal.aborted) return
    setPrevious(selected); setRun(next)
    setDraft({ ...clone(next.request), fields: clone(profiles), weather: clone(initialRequest.weather) })
    setDirty(false); polls.current = 0
    setAlternativeId(next.comparison?.alternatives[0]?.alternative_id ?? '')
    pendingSubmit.current = null
  }
  async function saveProfile(edited: FieldProfileSnapshot) {
    await operation(async signal => {
      const body = await profileWrite(active, edited, crypto.randomUUID(), clock())
      if (signal.aborted) return
      const saved = await api.saveProfile(body, signal)
      if (signal.aborted) return
      setProfiles(old => old.map(p => p.field_id === saved.field_id ? saved : p))
      setDraft(old => ({ ...old, fields: old.fields.map(p => p.field_id === saved.field_id ? saved : p) }))
      setDirty(true); setNotice('Profile saved'); onProfileSaved?.(saved)
    })
  }
  async function refreshCommitments(signal: AbortSignal) {
    if (!onApplied) throw new Error('Refresh confirmed commitments before replanning')
    const fresh = await onApplied(signal)
    if (signal.aborted) return
    setConfirmedLocks(clone(fresh.locks))
    setDraft(old => ({ ...old, locks: clone(fresh.locks) }))
    setNeedsCommitments(false); setDirty(true)
  }
  async function apply(signal: AbortSignal) {
    if (!run || !selected || !checked || dirty || staleRun(run, latestProfiles, initialRequest.weather, clock())) throw new Error('Stale report — refresh inputs and replan')
    const key = `${run.run_id}/${selected.alternative_id}/${run.input_sha256}`
    if (pendingApply.current?.key !== key) pendingApply.current = { key, id: crypto.randomUUID() }
    const result = await api.apply(applyRequest(run, selected.alternative_id, pendingApply.current.id), signal)
    if (signal.aborted) return
    if (result.status !== 'applied' && result.status !== 'already_applied') {
      setError(`${t('Application failed')}: ${t(result.status)}. ${result.reasons.map(r => r.message).join(' ')}`)
      return
    }
    setConfirm(false); setNotice('Plan applied'); setNeedsCommitments(true)
    if (onApplied) await refreshCommitments(signal)
  }
  const changeDraft = (value: FarmPlanningRequest) => { setDraft(value); setDirty(true); setNotice(''); setConfirm(false) }
  return <Container className="planning-workspace" lang={locale}>
    {embedded ? <header><h2>{t('Scenario comparison')}</h2><p>{t('Planning period')}: {draft.start_season}–{draft.start_season + draft.season_count - 1} · {t('No calibrated probabilities')}</p></header>
      : <header className="pl-heading"><div><p className="pl-eyebrow">FIELD SHIFT</p><h1>{t('Crop planning')}</h1></div><span>{initialRequest.farm_id}</span></header>}
    {synthetic && <p className="pl-banner">{t('Synthetic demonstration — not measured accuracy')}</p>}
    <div className="pl-row"><Select label={t('Field')} value={active?.field_id ?? ''} onChange={fieldId => onContextChange({ ...context, fieldId })}>
      {profiles.map(f => <option key={f.field_id} value={f.field_id}>{fieldName(f.field_id)}</option>)}</Select>
      <Select label={t('Season')} value={String(context.season)} onChange={season => onContextChange({ ...context, season: Number(season) })}>
        {Array.from({ length: draft.season_count }, (_, index) => draft.start_season + index).map(y => <option key={y}>{y}</option>)}</Select>
      <Select label={t('Scenario')} value={context.trajectoryId} onChange={trajectoryId => onContextChange({ ...context, trajectoryId })}>
        {initialRequest.weather.scenarios.map(s => <option key={s.trajectory_id} value={s.trajectory_id}>{s.trajectory_id}</option>)}</Select></div>
    {error && !confirm && <div className="pl-error" role="alert">{t(error)}<Reasons reasons={errorReasons} /></div>}
    {notice && <p role="status" className="pl-banner">{t(notice)}</p>}
    {dirty && run && <p role="status">{t('New inputs — calculate again')}</p>}
    {stale && <p className="pl-error" role="status">{t('Stale report — refresh inputs and replan')}</p>}
    {needsCommitments && <p role="status">{t('Refresh confirmed commitments before replanning')}
      {onApplied && <button className="button" disabled={busy} onClick={() => void operation(refreshCommitments)}>{t('Retry commitment refresh')}</button>}
    </p>}
    <PlanSettings compact={embedded} focus={focus} fieldNames={fieldNames} draft={draft} selectedFields={selectedFields} cropOptions={cropOptions} locale={locale} busy={busy || needsCommitments}
      lockedAtStart={[...initialRequest.locks, ...confirmedLocks, ...(run?.request.locks ?? [])]} onChange={changeDraft} onFields={ids => { setSelectedFields(ids); setDirty(true); setConfirm(false) }} onSubmit={() => void operation(submit)} replan={!!run} />
    <section className="pl-card"><div className="pl-row"><h2 role="status" aria-live="polite">{t(runLabel(run))}</h2>
      <button className="button subtle" disabled={busy} onClick={() => void operation(async signal => {
        const results = await api.list(initialRequest.farm_id, signal); if (!signal.aborted) setSavedRuns(results)
      })}>{t('Saved plans')}</button>
      {run && <button className="button subtle" disabled={busy} onClick={() => void operation(async signal => {
        const updated = await api.run(run.request.farm_id, run.run_id, signal); if (!signal.aborted) { setRun(updated); polls.current = 0 }
      })}>{t('Refresh')}</button>}</div>
      {savedRuns.length > 0 && <Select label={t('Saved plans')} value={run?.run_id ?? ''} onChange={id => {
        const saved = savedRuns.find(r => r.run_id === id)
        if (saved) { setRun(saved); setAlternativeId(saved.comparison?.alternatives[0]?.alternative_id ?? ''); setDirty(true); setConfirm(false) }
      }}><option value="">—</option>{savedRuns.map(r => <option key={r.run_id} value={r.run_id}>{r.request.start_season}–{r.request.start_season + r.request.season_count - 1} · {t(runLabel(r))} · {new Date(r.created_at).toLocaleString(locale)}</option>)}</Select>}
      {run?.error && <Reasons reasons={[run.error]} />}
      {run && <PlanComparison run={run} evidence={evidence} locale={locale} context={context} onContext={onContextChange} alternativeId={alternativeId} onAlternative={id => { setAlternativeId(id); setConfirm(false) }} previous={previous} fieldNames={fieldNames} compact={embedded} />}
      <button className="button primary" disabled={busy || needsCommitments || dirty || stale || run?.status !== 'succeeded' || !selected || !['optimal', 'feasible', 'limit_reached'].includes(run?.comparison?.solver_status ?? '')}
        onClick={() => setConfirm(true)}>{t('Apply selected plan')}</button>
    </section>
    <details open={focus === 'profiles' || !embedded}><summary>{t('Profiles and source evidence')}</summary>
    <FieldDataCards fields={profiles} weather={initialRequest.weather} selected={context.fieldId} onSelect={fieldId => onContextChange({ ...context, fieldId })} locale={locale} />
    {active && <details open={focus === 'profiles'}><summary>{t('Field profile')}</summary><FieldProfileEditor key={active.snapshot_id} profile={active} locale={locale} busy={busy} onSave={saveProfile} /></details>}
    </details>
    <dialog ref={dialog} className="pl-dialog" aria-labelledby={dialogTitle} onCancel={event => { event.preventDefault(); setConfirm(false) }}><h2 id={dialogTitle}>{t('Confirm application')}</h2>
      {error && confirm && <div className="pl-error" role="alert">{t(error)}<Reasons reasons={errorReasons} /></div>}
      <p>{selected?.label} · {run?.run_id}</p><p>{t('No guaranteed profit')}</p>
      <ul>{selected?.assignments.map(a => <li key={`${a.field_id}-${a.season}`}>{fieldName(a.field_id)} · {a.season} · {a.crop.crop_id}{a.locked ? ` · ${t('Locked')}: ${a.commitment_id}` : ''}</li>)}</ul>
      <label className="pl-check"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />{t('I reviewed the plan and commitments')}</label>
      <div className="pl-row"><button className="button primary" disabled={!checked || busy || stale || dirty} onClick={() => void operation(apply)}>{t('Confirm application')}</button>
        <button className="button subtle" disabled={busy} onClick={() => setConfirm(false)}>{t('Cancel')}</button></div>
    </dialog>
  </Container>
}
