import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowUturnLeftIcon, CheckIcon, TrashIcon } from '@heroicons/react/24/outline'
import type { Field, Position } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import type { FieldGeometryDraft } from './fieldGeometryDraft'
import { estimateHectares, parseVertices, polygonFrom } from './geometry'
import { readGeoJsonBoundary } from './geoJsonBoundary'

type Props = {
  draft: FieldGeometryDraft
  field?: Field
  season: number
  saving: boolean
  error?: string
  onCoverage: (fullField: boolean) => void
  onSelect: (index?: number) => void
  onReplace: (points: Position[], append: boolean) => void
  onUndo: () => void
  onSave?: () => void
  onReload?: () => void
  suggestionTargets?: Field[]
  onSuggestionTarget?: (fieldId?: string) => void
}

export function FieldGeometryEditor({
  draft, field, season, saving, error, onCoverage, onSelect, onReplace, onUndo, onSave, onReload,
  suggestionTargets, onSuggestionTarget,
}: Props) {
  const { t, number, date } = useLocale()
  const [coordinateError, setCoordinateError] = useState<string>()
  const [importing, setImporting] = useState(false)
  const importGeneration = useRef(0)
  useEffect(() => () => { importGeneration.current++ }, [])
  const area = draft.fullField && field ? field.area_ha : estimateHectares(draft.points)
  const shapeComplete = draft.fullField || draft.points.length >= 3
  const isPlanting = draft.mode === 'planting'
  const coordinates = draft.points.map(([longitude, latitude]) => longitude + ',' + latitude).join('\n')

  function applyCoordinates(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      const points = polygonFrom(parseVertices(String(new FormData(event.currentTarget).get('coordinates') ?? '')))
        .coordinates[0].slice(0, -1)
      onReplace(points, false)
      setCoordinateError(undefined)
    } catch (failure) {
      setCoordinateError(errorText(failure))
    }
  }

  function removeVertex() {
    if (draft.active === undefined) return
    onReplace(draft.points.filter((_, index) => index !== draft.active), draft.append)
  }

  async function importBoundary(file: File) {
    const generation = ++importGeneration.current
    setImporting(true)
    try {
      const { points } = await readGeoJsonBoundary(file)
      if (generation !== importGeneration.current) return
      onReplace(points, false)
      setCoordinateError(undefined)
    } catch (failure) {
      if (generation === importGeneration.current) setCoordinateError(errorText(failure))
    } finally {
      if (generation === importGeneration.current) setImporting(false)
    }
  }

  return (
    <div className="field-geometry-editor" aria-label={t(isPlanting ? 'Edit planted area' : 'Edit boundary')}>
      <div className="field-editor-summary">
        <div>
          <p>{field?.name ?? t('New field')}{(isPlanting || draft.effectiveSeason !== undefined) && <> · {t('Season')} {draft.effectiveSeason ?? season}</>}</p>
          <strong>{shapeComplete ? number(area, 2) + ' ' + t('ha') : '—'}</strong>
          <small>{t(draft.fullField ? 'Total field area' : 'Area preview')}</small>
        </div>
        {isPlanting && (
          <div className="field-coverage-switch" role="group" aria-label={t('Planting coverage')}>
            <button type="button" aria-pressed={!draft.fullField} disabled={saving || importing} onClick={() => onCoverage(false)}>
              {t('Part of field')}
            </button>
            <button type="button" aria-pressed={draft.fullField} disabled={saving || importing} onClick={() => onCoverage(true)}>
              {t('Entire field')}
            </button>
          </div>
        )}
      </div>
      {draft.suggestion && <div className="field-suggestion-review">
        <p className="field-editor-suggestion" role="status">
          {t(draft.suggestion.source === 'aerial' ? 'Suggested from USGS aerial imagery.' : 'Suggested from Sentinel-2 imagery.')}
          {draft.suggestion.preview?.acquiredAt && (
            <> · <time dateTime={draft.suggestion.preview.acquiredAt}>{date(draft.suggestion.preview.acquiredAt)}</time></>
          )}
        </p>
        {Boolean(suggestionTargets?.length) && <label className="control">
          <span>{t('Save contour to')}</span>
          <select value={draft.fieldId ?? ''} disabled={saving}
            onChange={(event) => onSuggestionTarget?.(event.target.value || undefined)}>
            <option value="">{t('New field')}</option>
            {suggestionTargets?.map((target) => <option key={target.id} value={target.id}>
              {target.name} · {target.id}
            </option>)}
          </select>
        </label>}
      </div>}
      {draft.catalogue && <p className="analysis-description"><a href={draft.catalogue.sourceUrl} target="_blank" rel="noreferrer">{draft.catalogue.source}</a> · {draft.catalogue.year} · {t('Review suggested boundary')}</p>}
      {!draft.fullField && (
        <>
          <p className="field-editor-instructions">{t(draft.append
            ? 'Click the map to add at least three vertices.'
            : 'Drag a vertex to move it. Select an edge marker to add a vertex.')}</p>
          <div className="field-editor-actions">
            <label className="control">
              <span>{t('Vertex')}</span>
              <select value={draft.active ?? ''} disabled={!draft.points.length || saving}
                onChange={(event) => onSelect(event.target.value === '' ? undefined : Number(event.target.value))}>
                <option value="">{t('Choose a vertex')}</option>
                {draft.points.map((_, index) => <option key={index} value={index}>{index + 1}</option>)}
              </select>
            </label>
            <button type="button" className="button subtle" onClick={removeVertex}
              disabled={saving || draft.active === undefined || (!draft.append && draft.points.length <= 3)}>
              <TrashIcon aria-hidden="true" />{t('Remove vertex')}
            </button>
            <button type="button" className="button subtle" onClick={onUndo} disabled={saving || !draft.undo.length}>
              <ArrowUturnLeftIcon aria-hidden="true" />{t('Undo')}
            </button>
            <button type="button" className="button subtle" onClick={() => onReplace([], true)} disabled={saving || !draft.points.length}>
              {t('Redraw contour')}
            </button>
          </div>
          {draft.append && (
            <div className="field-editor-actions">
              {isPlanting && field && !draft.points.length && (
                <button type="button" className="button subtle" disabled={saving}
                  onClick={() => onReplace(field.geometry.coordinates[0].slice(0, -1), false)}>
                  {t('Start from field boundary')}
                </button>
              )}
              {draft.points.length >= 3 && (
                <button type="button" className="button subtle" disabled={saving} onClick={() => onReplace(draft.points, false)}>
                  {t('Finish drawing')}
                </button>
              )}
            </div>
          )}
          <details className="field-editor-coordinates">
            <summary>{t('Enter coordinates')}</summary>
            <form onSubmit={applyCoordinates}>
              <label>
                <span>{t('Vertices (longitude,latitude, one pair per line)')}</span>
                <textarea key={coordinates} name="coordinates" rows={4} maxLength={7000} defaultValue={coordinates} required disabled={saving} />
              </label>
              <button type="submit" className="button subtle" disabled={saving}>{t('Apply coordinates')}</button>
            </form>
            <label className="field-geojson-file">
              <span>{t('Import boundary GeoJSON')}</span>
              <input type="file" accept=".geojson,.json,application/geo+json" disabled={saving || importing} onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (file) void importBoundary(file)
              }} />
            </label>
            {coordinateError && <ErrorBox message={t(coordinateError)} />}
          </details>
        </>
      )}
      {error && <ErrorBox message={error} />}
      <div className="field-editor-actions">
        {onSave && <button type="button" className="button primary" disabled={!shapeComplete || saving || importing} onClick={onSave}>
          <CheckIcon aria-hidden="true" />{t(saving ? 'Saving…' : isPlanting ? 'Save planted area' : 'Save boundary')}
        </button>}
        {onReload && <button type="button" className="button subtle" onClick={onReload}>{t('Reload field')}</button>}
      </div>
    </div>
  )
}
