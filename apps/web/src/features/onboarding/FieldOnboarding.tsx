import { useEffect, useRef, useState } from 'react'
import { ArrowLeftIcon } from '@heroicons/react/24/outline'
import type { Field } from '../../api/contracts'
import { onboardingApi, type OnboardingSetup } from '../../api/onboarding'
import { useLocale } from '../../shared/locale'
import { ErrorBox, Loading } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import { FieldSetupMap } from './FieldSetupMap'
import { OnboardingChat } from './OnboardingChat'
import {
  draftFromInputs, finishSetupDraft, hasLocalSetupDraft, inputsFromDraft, readSetupDraft, saveSetupDraft, type SetupDraft,
} from './setupStorage'
import './fieldOnboarding.css'

type Props = {
  id: string
  fields: Field[]
  season: number
  onCancel: () => void
  onComplete: (field: Field) => void
}

export function FieldOnboarding({ id, fields, season, onCancel, onComplete }: Props) {
  const { language, t } = useLocale()
  const uk = language === 'uk'
  const [draft, setDraft] = useState(() => readSetupDraft(id))
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<{ load?: string; save?: string }>({})
  const [retry, setRetry] = useState(0)
  const [startSeason, setStartSeason] = useState(season + 1)
  const busyRef = useRef(false)
  const draftRef = useRef(draft)
  const completion = useRef(onComplete)
  completion.current = onComplete

  function update(next: SetupDraft) {
    draftRef.current = next
    setDraft(next)
    saveSetupDraft(id, next)
  }

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setErrors({})
    onboardingApi.get(id, controller.signal).then((setup) => {
      if (controller.signal.aborted) return
      if (setup?.status === 'completed' && setup.field) {
        finishSetupDraft(id, draftFromInputs(setup.inputs).location)
        completion.current(setup.field)
        return
      }
      if (setup?.inputs.start_season) setStartSeason(setup.inputs.start_season)
      // The local copy includes any edits made after the last successful server save.
      if (setup && !hasLocalSetupDraft(id)) {
        const restored = draftFromInputs(setup.inputs)
        draftRef.current = restored
        setDraft(restored)
      }
    }).catch((error) => {
      if (!controller.signal.aborted) setErrors({ load: errorText(error) })
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [id, retry])

  const mounted = useRef(false)
  const savedAnswers = useRef<Promise<unknown>>(Promise.resolve())
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  async function save(complete: boolean) {
    if (busyRef.current || !draft.geometry || !draft.location.regionCode) return
    busyRef.current = true
    setBusy(true)
    setErrors({})
    try {
      await savedAnswers.current
      const saved = await onboardingApi.save(id, inputsFromDraft(draft, startSeason))
      const result: OnboardingSetup = complete ? await onboardingApi.complete(id) : saved
      if (!mounted.current) return
      if (result.status === 'completed' && result.field) {
        finishSetupDraft(id, draft.location)
        completion.current(result.field)
      } else {
        update({ ...draft, step: 'chat' })
        window.scrollTo(0, 0)
      }
    } catch (error) {
      if (mounted.current) setErrors({ save: errorText(error) })
    } finally {
      busyRef.current = false
      if (mounted.current) setBusy(false)
    }
  }

  function updateAnswers(answers: SetupDraft['answers']) {
    const next = { ...draftRef.current, answers }
    update(next)
    setErrors({})
    savedAnswers.current = savedAnswers.current.then(() =>
      onboardingApi.save(id, inputsFromDraft(next, startSeason)),
    ).catch(() => {
      // The local draft holds the latest answers. Confirming the plan retries the full draft.
    })
  }

  const label = uk ? 'Нове поле' : 'New field'
  return (
    <div className="page-body field-onboarding">
      <header className="field-onboarding-header">
        <button type="button" className="button subtle" onClick={onCancel} disabled={busy}>
          <ArrowLeftIcon aria-hidden="true" />{uk ? 'До полів' : 'Back to fields'}
        </button>
        <div>
          <h1>{uk ? 'Додати поле' : 'Add a field'}</h1>
          <p>{draft.step === 'map'
            ? (uk ? 'Знайдіть поле на карті й перевірте запропоновану межу.' : 'Find your field on the map and review the suggested boundary.')
            : (uk ? 'Помічник збере дані для плану сівозміни.' : 'The assistant will gather details for your rotation plan.')}</p>
        </div>
      </header>
      {loading ? <Loading text={uk ? 'Завантажуємо чернетку…' : 'Loading your draft…'} />
        : errors.load ? <ErrorBox message={errors.load} retry={() => setRetry(retry + 1)} />
        : draft.step === 'chat' && draft.geometry ? (
          <OnboardingChat fieldName={draft.name.trim() || label} season={startSeason} answers={draft.answers}
            onAnswersChange={updateAnswers} busy={busy} error={errors.save}
            onBack={() => { setErrors({}); update({ ...draft, step: 'map' }); window.scrollTo(0, 0) }}
            onComplete={() => void save(true)} />
        ) : (
          <>
            {errors.save && <ErrorBox message={errors.save} />}
            {draft.geometry && <label className="field-onboarding-name control">
              <span>{uk ? 'Назва поля (необов’язково)' : 'Field name (optional)'}</span>
              <input value={draft.name} placeholder={label} maxLength={120} disabled={busy}
                onChange={(event) => update({ ...draft, name: event.target.value })} />
            </label>}
            <FieldSetupMap location={draft.location} geometry={draft.geometry} fields={fields} season={season} disabled={busy}
              onLocationChange={(location) => update({
                ...draftRef.current, location, answers: draftRef.current.geometry ? draftRef.current.answers : {},
              })}
              onGeometryChange={(geometry) => {
                const latest = draftRef.current
                update({ ...latest, geometry })
              }}
              onConfirm={() => void save(false)}
              confirmLabel={busy ? t('Saving:') : (uk ? 'Підтвердити межу й перейти до питань' : 'Confirm boundary and continue')} />
          </>
        )}
    </div>
  )
}
