import { useEffect, useRef, useState, type FormEvent, type Ref } from 'react'
import { CheckCircleIcon, DocumentTextIcon, MapPinIcon, PhotoIcon, PlusIcon } from '@heroicons/react/24/outline'
import { api, ApiError } from '../../api/client'
import type { Field, NewObservation, Observation, ObservationCategory } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import { prepareScoutingPhoto } from './scoutingPhoto'
import { useAccess } from '../access/AccessProvider'

const categories: Record<ObservationCategory, string> = {
  general: 'General', crop_condition: 'Crop condition', weeds: 'Weeds', pests: 'Pests',
  disease: 'Disease', water: 'Water issue', soil: 'Soil condition',
}

type ObservationDraft = NewObservation & { client_id: string; date: string }
const draftStorageKey = 'field-shift-scouting-draft-v1'

function readDraft(storageKey = draftStorageKey): ObservationDraft | undefined {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw || raw.length > 720_000) return
    const value = JSON.parse(raw) as ObservationDraft | null
    if (value && typeof value.client_id === 'string' && typeof value.field_id === 'string' &&
      typeof value.text === 'string' && typeof value.date === 'string') return value
  } catch { /* A malformed or unavailable local draft cannot be restored. */ }
}

function storeDraft(draft: ObservationDraft, storageKey: string) {
  try { localStorage.setItem(storageKey, JSON.stringify(draft)); return true }
  catch { return false }
}

function clearDraft(draft: ObservationDraft, storageKey: string) {
  try {
    if (readDraft(storageKey)?.client_id === draft.client_id) localStorage.removeItem(storageKey)
  } catch { /* Storage may be unavailable. */ }
}

type ObservationEditor = { fieldId?: string } | null

export function FarmObservations({ fields, observations, openField, onSaved, editor, setEditor, sectionRef }: {
  fields: Field[]
  observations: Observation[]
  openField: (id: string) => void
  onSaved: (observation: Observation) => void
  editor: ObservationEditor
  setEditor: (editor: ObservationEditor) => void
  sectionRef: Ref<HTMLElement>
}) {
  const { t, date } = useLocale()
  const { canEdit, session } = useAccess()
  const storageKey = session?.mode === 'team' ? draftStorageKey + ':user:' + session.user?.id : draftStorageKey
  const [saved, setSaved] = useState(false)
  const [queued, setQueued] = useState<ObservationDraft | undefined>(() => readDraft(storageKey))
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState<string>()
  const editing = canEdit && editor !== null && !queued
  const trigger = useRef<HTMLButtonElement>(null)
  const wasEditing = useRef(false)
  const ordered = [...observations].sort((left, right) => right.date.localeCompare(left.date) || right.id - left.id)
  useEffect(() => {
    if (wasEditing.current && !editing && !queued) trigger.current?.focus({ preventScroll: true })
    wasEditing.current = editing
  }, [editing, queued])

  function savedObservation(observation: Observation) {
    setSaved(true)
    setEditor(null)
    onSaved(observation)
  }

  async function retrySync() {
    if (!queued || syncing || !canEdit) return
    setSyncing(true)
    setSyncError(undefined)
    try {
      const observation = await api.addObservation(queued)
      clearDraft(queued, storageKey)
      setQueued(undefined)
      savedObservation(observation)
    } catch (failure) { setSyncError(errorText(failure)) }
    finally { setSyncing(false) }
  }
  return (
    <section ref={sectionRef} className="farm-section farm-observations" aria-labelledby="farm-observations-heading">
      <div className="farm-section-heading">
        <div><h3 id="farm-observations-heading">{t('Observations')}</h3><p>{t('Notes from the field, in one place.')}</p></div>
        <button ref={trigger} type="button" className="button" disabled={!canEdit || !fields.length || editing || !!queued} aria-expanded={editing} aria-controls="farm-observation-form"
          onClick={() => { setEditor({}); setSaved(false) }}><PlusIcon aria-hidden="true" />{t('Add observation')}</button>
      </div>
      {editing && <ObservationForm storageKey={storageKey} fields={fields} fieldId={editor?.fieldId} onCancel={() => setEditor(null)} onSaved={savedObservation}
        onQueued={(draft) => { setEditor(null); setQueued(draft); setSyncError(undefined) }} />}
      {queued && <div className="farm-observation-draft">
        <p role="status"><strong>{t('Observation saved on this device')}</strong></p>
        <p>{queued.text}</p>
        <p>{t('Sync this observation when the connection returns.')}</p>
        {syncError && <ErrorBox message={syncError} />}
        <div className="farm-form-actions">
          <button type="button" className="button" disabled={syncing} onClick={() => {
            clearDraft(queued, storageKey)
            setQueued(undefined)
            setEditor(null)
          }}>{t('Discard local draft')}</button>
          <button type="button" className="button primary" disabled={syncing || !canEdit} onClick={() => void retrySync()}>
            {t(syncing ? 'Syncing…' : 'Sync observation')}
          </button>
        </div>
      </div>}
      {saved && !editing && !queued && <p className="farm-save-notice" role="status"><CheckCircleIcon aria-hidden="true" />{t('Observation saved.')}</p>}
      {ordered.length > 0 ? <ol className="farm-observation-timeline">
        {ordered.map((item) => {
          const Icon = item.has_photo || item.kind === 'soil_photo' ? PhotoIcon : DocumentTextIcon
          const field = fields.find(({ id }) => id === item.field_id)
          return <li key={item.id}>
            <span className="farm-observation-marker"><Icon aria-hidden="true" /></span>
            <div className="farm-observation-entry">
              <div className="farm-observation-meta">
                {field ? <button type="button" onClick={() => openField(field.id)}>{field.name}</button> : <strong>{item.field_id}</strong>}
                <span>{t(item.category ? categories[item.category] : item.kind === 'soil_photo' ? 'Soil photo' : 'Field note')}</span>
                <time dateTime={item.date}>{date(item.date)}</time>
              </div>
              <p>{item.text}</p>
              {item.latitude !== undefined && item.longitude !== undefined && <div className="farm-scouting-location">
                <MapPinIcon aria-hidden="true" />
                <span>{t('Location')}: {item.latitude.toFixed(5)}, {item.longitude.toFixed(5)}</span>
              </div>}
              {item.has_photo && <ObservationPhoto id={item.id} />}
            </div>
          </li>
        })}
      </ol> : !editing && <div className="farm-empty farm-observations-empty"><DocumentTextIcon aria-hidden="true" /><p>{t('No observations recorded yet.')}</p></div>}
    </section>
  )
}

function ObservationPhoto({ id }: { id: number }) {
  const { t } = useLocale()
  const [opened, setOpened] = useState(false)
  const [failed, setFailed] = useState(false)
  return <details className="farm-scouting-photo" onToggle={(event) => {
    if (event.currentTarget.open) setOpened(true)
  }}>
    <summary>{t('View observation photo')}</summary>
    {opened && (failed
      ? <p role="status">{t('The observation photo could not be loaded.')}</p>
      : <img src={api.observationPhoto(id)} alt={t('Observation photo')} loading="lazy" onError={() => setFailed(true)} />)}
  </details>
}

function ObservationForm({ fields, fieldId, onSaved, onCancel, onQueued, storageKey }: {
  storageKey: string
  fields: Field[]
  fieldId?: string
  onSaved: (observation: Observation) => void
  onCancel: () => void
  onQueued: (draft: ObservationDraft) => void
}) {
  const { t } = useLocale()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string>()
  const [locating, setLocating] = useState(false)
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const fieldSelect = useRef<HTMLSelectElement>(null)
  const lastAttempt = useRef<{ signature: string; clientId: string }>(null)
  useEffect(() => { fieldSelect.current?.focus({ preventScroll: true }) }, [])
  function locate() {
    if (!navigator.geolocation) { setError('Device location is unavailable.'); return }
    setLocating(true)
    setError(undefined)
    navigator.geolocation.getCurrentPosition(({ coords }) => {
      setLatitude(coords.latitude.toFixed(6))
      setLongitude(coords.longitude.toFixed(6))
      setLocating(false)
    }, () => {
      setError('Location was not available. Enter coordinates manually or leave them blank.')
      setLocating(false)
    }, { timeout: 10_000, maximumAge: 30_000 })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || locating) return
    const values = new FormData(event.currentTarget)
    const note = String(values.get('note') ?? '').trim()
    if (!note) { setError('Enter an observation before saving.'); return }
    if (Boolean(latitude) !== Boolean(longitude)) { setError('Provide both latitude and longitude.'); return }
    setPending(true)
    setError(undefined)
    try {
      const file = values.get('photo')
      const photo = file instanceof File && file.size ? await prepareScoutingPhoto(file) : undefined
      const category = values.get('category') as ObservationCategory
      const observation: NewObservation & { date: string } = {
        field_id: String(values.get('field') ?? ''),
        kind: category === 'soil' && photo ? 'soil_photo' : 'field_note',
        category,
        text: note,
        date: String(values.get('date') || new Date().toISOString().slice(0, 10)),
        latitude: latitude ? Number(latitude) : undefined,
        longitude: longitude ? Number(longitude) : undefined,
        photo_jpeg: photo,
      }
      const signature = JSON.stringify(observation)
      if (lastAttempt.current?.signature !== signature) {
        lastAttempt.current = { signature, clientId: crypto.randomUUID() }
      }
      const draft = { ...observation, client_id: lastAttempt.current.clientId }
      const persisted = storeDraft(draft, storageKey)
      try {
        const result = await api.addObservation(draft)
        clearDraft(draft, storageKey)
        onSaved(result)
      } catch (failure) {
        const retryable = !(failure instanceof ApiError) || failure.status >= 500 || failure.status === 429
        if (retryable && persisted) onQueued(draft)
        else { clearDraft(draft, storageKey); throw failure }
      }
    } catch (failure) { setError(errorText(failure)) }
    finally { setPending(false) }
  }
  return <form id="farm-observation-form" className="farm-observation-form" onSubmit={(event) => void submit(event)}
    onKeyDown={(event) => { if (event.key === 'Escape' && !pending) { event.stopPropagation(); onCancel() } }}>
    <fieldset disabled={pending}>
      <legend className="sr-only">{t('Add observation')}</legend>
      <label><span>{t('Field')}</span><select ref={fieldSelect} name="field" defaultValue={fieldId} required>{fields.map((field) => <option key={field.id} value={field.id}>{field.name}</option>)}</select></label>
      <label><span>{t('Date')} <small>{t('Optional')}</small></span><input name="date" type="date" /></label>
      <label><span>{t('Category')}</span><select name="category" defaultValue="general">
        {Object.entries(categories).map(([id, label]) => <option key={id} value={id}>{t(label)}</option>)}
      </select></label>
      <label><span>{t('Photo')} <small>{t('Optional')}</small></span>
        <input name="photo" type="file" accept="image/jpeg,image/png,image/webp" />
      </label>
      <label className="farm-form-wide"><span>{t('Note')}</span><textarea name="note" rows={3} required maxLength={2000} /></label>
      <details className="farm-form-wide farm-scouting-coordinates" onInvalid={(event) => { event.currentTarget.open = true }}>
        <summary>{t('Location')} <small>{t('Optional')}</small></summary>
        <div className="farm-scouting-coordinate-inputs">
          <label><span>{t('Latitude')}</span><input type="number" min={-90} max={90} step="any" value={latitude}
            onChange={(event) => setLatitude(event.target.value)} /></label>
          <label><span>{t('Longitude')}</span><input type="number" min={-180} max={180} step="any" value={longitude}
            onChange={(event) => setLongitude(event.target.value)} /></label>
          <button type="button" className="button" onClick={locate} disabled={locating}>
            <MapPinIcon aria-hidden="true" />{t(locating ? 'Finding location…' : 'Use my location')}
          </button>
        </div>
      </details>
    </fieldset>
    {error && <ErrorBox message={error} />}
    <div className="farm-form-actions"><button type="button" className="button" disabled={pending} onClick={onCancel}>{t('Cancel')}</button><button type="submit" className="button primary" disabled={pending || locating}>{pending ? t('Saving…') : t('Save observation')}</button></div>
  </form>
}
