import { useState, type FormEvent } from 'react'
import { CheckCircleIcon, InformationCircleIcon } from '@heroicons/react/24/outline'
import { api } from '../../api/client'
import type { FarmDefaults } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import { useAccess } from '../access/AccessProvider'

function draftFrom(defaults: FarmDefaults) {
  return { irrigation: defaults.irrigation_available, priority: defaults.priority, equipment: defaults.equipment ?? '', budget: defaults.budget == null ? '' : String(defaults.budget) }
}

export function PlanningDefaults({ defaults, onSaved }: { defaults: FarmDefaults; onSaved: (saved: FarmDefaults) => void }) {
  const { canEdit } = useAccess()
  const { t } = useLocale()
  const [draft, setDraft] = useState(() => draftFrom(defaults))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string>()
  const [saved, setSaved] = useState(false)
  const original = draftFrom(defaults)
  const dirty = draft.irrigation !== original.irrigation || draft.priority !== original.priority
    || draft.equipment !== original.equipment || draft.budget !== original.budget

  function update(values: Partial<typeof draft>) {
    setDraft((current) => ({ ...current, ...values }))
    setSaved(false)
    setError(undefined)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !dirty || !canEdit) return
    const priority = draft.priority.trim()
    if (!priority) { setError('Enter a planning priority.'); return }
    setPending(true)
    setError(undefined)
    setSaved(false)
    try {
      const result = await api.saveDefaults({
        irrigation_available: draft.irrigation, priority,
        equipment: draft.equipment.trim() || null,
        budget: draft.budget.trim() ? Number(draft.budget) : null,
      })
      setDraft(draftFrom(result))
      setSaved(true)
      onSaved(result)
    } catch (failure) { setError(errorText(failure)) }
    finally { setPending(false) }
  }

  return (
    <section className="farm-planning-section" aria-labelledby="farm-planning-heading">
      <div className="farm-intro"><div><h2 id="farm-planning-heading">{t('Planning defaults')}</h2><p>{t('Used for new scenarios on this farm.')}</p></div></div>
      <div className="farm-planning-grid">
        <form className="farm-section farm-planning-form" onSubmit={(event) => void submit(event)}>
          <fieldset disabled={pending || !canEdit}>
            <legend className="sr-only">{t('Planning defaults')}</legend>
            <label><span>{t('Irrigation')}</span><select name="irrigation" value={draft.irrigation ? 'available' : 'unavailable'} onChange={(event) => update({ irrigation: event.target.value === 'available' })}>
              <option value="unavailable">{t('Not available')}</option><option value="available">{t('Available')}</option>
            </select></label>
            <label><span>{t('Priority')}</span><input name="priority" value={draft.priority} onChange={(event) => update({ priority: event.target.value })} maxLength={120} required /></label>
            <label><span>{t('Equipment')} <small>{t('Optional')}</small></span><input name="equipment" value={draft.equipment} onChange={(event) => update({ equipment: event.target.value })} maxLength={240} placeholder={t('Add equipment')} /></label>
            <label><span>{t('Budget')} <small>{t('Optional')}</small></span><input name="budget" type="number" min="0" max="10000000000" step=".01" value={draft.budget} onChange={(event) => update({ budget: event.target.value })} placeholder={t('Not specified')} /></label>
          </fieldset>
          {error && <ErrorBox message={error} />}
          {saved && <p className="farm-save-notice" role="status"><CheckCircleIcon aria-hidden="true" />{t('Planning defaults saved.')}</p>}
          <div className="farm-form-actions">
            {dirty && <button type="button" className="button" disabled={pending} onClick={() => { setDraft(draftFrom(defaults)); setError(undefined); setSaved(false) }}>{t('Discard changes')}</button>}
            <button type="submit" className="button primary" disabled={pending || !dirty || !canEdit}>{pending ? t('Saving…') : t('Save changes')}</button>
          </div>
        </form>
        <aside className="farm-planning-note"><InformationCircleIcon aria-hidden="true" />
          <div><h3>{t('A starting point for planning')}</h3><p>{t('Save your farm’s resources and priorities here. These settings provide context for new scenarios.')}</p><p>{t('Leave optional values blank when they are not known.')}</p></div>
        </aside>
      </div>
    </section>
  )
}
