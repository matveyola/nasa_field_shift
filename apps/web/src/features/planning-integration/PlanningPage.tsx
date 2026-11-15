import { useCallback, useState } from 'react'
import { planningBootstrap, planningEvidence, initializePlanningProfile } from '../../api/planningWorkspace'
import { createPlanningApi } from '../../api/planningV1'
import type { FieldProfileSnapshot, PlanningRun } from '../../api/planningV1.generated'
import { PlanningWorkspace } from '../planning'
import { FieldProfileEditor } from '../field-profile/FieldProfileEditor'
import { profileWrite } from '../field-profile/model'
import { useResource } from '../../shared/useResource'
import { useLocale } from '../../shared/locale'
import { Loading, ErrorBox } from '../../shared/ui'

const api = createPlanningApi()
export function PlanningPage({ fieldId, season, trajectory, onContext, embedded = false, fieldNames, focus }: {
  fieldId: string; season: number; trajectory?: string; embedded?: boolean; fieldNames?: Record<string, string>; focus?: 'profiles' | 'economics';
  onContext: (fieldId: string, season: number, trajectory: string) => void
}) {
  const { language } = useLocale()
  const uk = language === 'uk'
  const data = useResource(planningBootstrap)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState(false)
  const [busy, setBusy] = useState(false)
  const loadEvidence = useCallback((run: PlanningRun, signal: AbortSignal) => planningEvidence(run.request.farm_id, run.run_id, signal), [])
  async function edit(profile: FieldProfileSnapshot) {
    const original = data.data?.profiles.find(p => p.field_id === profile.field_id)
    if (!original) return
    setBusy(true); setError('')
    try { await api.saveProfile(await profileWrite(original, profile, crypto.randomUUID(), new Date())); data.reload() }
    catch (error) { setError(error instanceof Error ? error.message : 'Profile unavailable') }
    finally { setBusy(false) }
  }
  if (data.pending) return <Loading text={uk ? 'Завантаження планування…' : 'Loading planning…'} />
  if (data.error) return <section><h2>{uk ? 'Сценарний розрахунок недоступний' : 'Scenario calculation unavailable'}</h2><p>{uk ? 'Можна продовжити розподіл культур. Для прогнозу потрібні паспорти, погодні сценарії, регіональні правила та параметри моделі; створення паспорта саме по собі цього не забезпечує.' : 'You can continue crop allocation. Forecasting needs profiles, weather scenarios, regional rules and model parameters; creating a profile alone does not provide these.'}</p><ErrorBox message={data.error} retry={data.reload} /></section>
  if (!data.data) return null
  const boot = data.data
  const request = boot.request
  return <section>
    <p role="status">{uk ? 'Сценарні розрахунки. Точність урожайності на незалежних польових даних ще не підтверджена.' : 'Scenario calculations. Yield accuracy has not been validated on independent field data.'}</p>
    {applied && <p role="status">{uk ? 'План застосовано. Підтверджені культури збережено.' : 'Plan applied. Confirmed crops are saved.'}</p>}
    {error && <p role="alert">{error}</p>}
    {request ? <PlanningWorkspace key={boot.configuration ?? request.request_id} initialRequest={request}
      cropOptions={boot.crop_options} currentProfiles={boot.profiles} locale={language} embedded={embedded} fieldNames={fieldNames} focus={focus}
      context={{ fieldId, season, trajectoryId: trajectory ?? request.weather.scenarios[0]?.trajectory_id ?? '' }}
      onContextChange={next => onContext(next.fieldId, next.season, next.trajectoryId)}
      loadEvidence={loadEvidence} onProfileSaved={data.reload} onApplied={async signal => {
        setApplied(true)
        const next = await planningBootstrap(signal)
        if (!next.request) throw new Error(next.reason)
        data.update(() => next)
        return next.request
      }} />
      : <>
        <h2>{uk ? 'Прогноз ще не готовий до порівняння' : 'Forecasts are not ready to compare'}</h2>
        <p>{uk ? 'Потрібні актуальні паспорти полів, сценарії погоди, перевірені регіональні правила та параметри моделі. За відсутності даних рекомендація не розраховується.' : 'Current field profiles, weather scenarios, reviewed regional rules and model parameters are required. Missing data does not produce a recommendation.'}</p>
        <p>{uk ? 'Розподіл культур доступний окремо. Паспорт поля — лише один із необхідних кроків; його створення не додає погодних сценаріїв, правил чи параметрів моделі.' : 'Crop allocation remains available separately. A field profile is only one prerequisite; creating it does not supply weather scenarios, rules or model parameters.'}</p>
        <details><summary>{uk ? 'Причина недоступності' : 'Availability details'}</summary><p>{boot.status}: {boot.reason}</p></details>
        <button className="button" onClick={data.reload}>{uk ? 'Перевірити дані' : 'Refresh inputs'}</button>
        <details open={focus === 'profiles'}><summary>{uk ? 'Паспорти полів та перевірка вхідних даних' : 'Field profiles and input review'}</summary>
        {boot.missing_profiles.map(field => <article key={field.field_id}><h2>{field.name}</h2>
          {['not_found', 'stale'].includes(field.code) ? <button disabled={busy} className="button" onClick={async () => {
            setBusy(true); setError('')
            try { await initializePlanningProfile(field.field_id, field.code === 'stale'); data.reload() }
            catch (e) { setError(e instanceof Error ? e.message : 'Profile unavailable') }
            finally { setBusy(false) }
          }}>{field.code === 'stale' ? (uk ? 'Створити паспорт для нових меж — дані треба перевірити знову' : 'Create profile for new boundary — inputs need review') : (uk ? 'Створити паспорт поля' : 'Create field profile')}</button> : <p>{uk ? 'Межі поля змінилися. Оновіть паспорт і пов’язані джерела даних.' : 'Field boundary changed. Refresh the profile and linked inputs.'}</p>}</article>)}
        {boot.profiles.map(profile => <FieldProfileEditor key={profile.snapshot_id} profile={profile} locale={language} busy={busy} onSave={edit} />)}
        </details>
      </>}
  </section>
}
