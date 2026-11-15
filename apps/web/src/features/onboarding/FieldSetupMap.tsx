import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowPathIcon, CheckIcon, MapPinIcon, PencilSquareIcon, PhotoIcon } from '@heroicons/react/24/outline'
import type { Field, Polygon, Position } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { FieldGeometryEditor } from '../fields/FieldGeometryEditor'
import type { BasemapId } from '../fields/basemaps'
import { fieldGeometryDraft, recordShape, undoShape, type FieldGeometryDraft } from '../fields/fieldGeometryDraft'
import { estimateHectares, polygonFrom } from '../fields/geometry'
import { useBoundarySuggestion } from '../fields/useBoundarySuggestion'
import type { SatelliteRaster } from '../fields/satelliteRaster'
import type { MapWindow } from '../fields/PreparedContoursPanel'
import { parseSetupCoordinates, setupCountries, setupRegion, setupRegions, type SetupLocation } from './locations'
import { LocationPicker } from './LocationPicker'
import { reviewedBoundary } from './reviewGeometry'
import '../fields/map.css'
import '../fields/fieldWorkspace.css'
import './fieldSetupMap.css'

const FieldMap = lazy(() => import('../fields/FieldMap'))
const noFields: Field[] = []

export type FieldSetupMapProps = {
  location: SetupLocation
  onLocationChange: (location: SetupLocation) => void
  geometry: Polygon | null
  onGeometryChange: (geometry: Polygon | null) => void
  onConfirm: () => void
  fields?: Field[]
  season?: number
  disabled?: boolean
  confirmLabel?: string
}

function draftFrom(geometry: Polygon | null): FieldGeometryDraft {
  return { ...fieldGeometryDraft(undefined, 'boundary'),
    points: geometry?.coordinates[0].slice(0, -1) ?? [], append: !geometry }
}

/** The reviewed shape stays in the host's onboarding draft; this component never creates a field. */
export function FieldSetupMap({ location, onLocationChange, geometry, onGeometryChange, onConfirm,
  fields = noFields, season = new Date().getUTCFullYear(), disabled = false, confirmLabel }: FieldSetupMapProps) {
  const { language, number } = useLocale()
  const uk = language === 'uk'
  const text = (ukText: string, enText: string) => uk ? ukText : enText
  const { countryCode, regionCode } = location
  const [draft, setDraft] = useState(() => draftFrom(geometry))
  const [mode, setMode] = useState<'automatic' | 'manual' | 'review'>(() => geometry ? 'review' : 'automatic')
  const [editing, setEditing] = useState(false)
  const [preview, setPreview] = useState<SatelliteRaster>()
  const [source, setSource] = useState<string>()
  const [error, setError] = useState<string>()
  const [retryPosition, setRetryPosition] = useState<Position>()
  const [busy, setBusy] = useState<'outline' | 'imagery'>()
  const [viewport, setViewport] = useState<MapWindow>()
  const [tileFailure, setTileFailure] = useState(false)
  const [basemap, setBasemap] = useState<BasemapId>('aerial')
  const [retryTiles, setRetryTiles] = useState(0)
  const [recenter, setRecenter] = useState(0)
  const [coordinates, setCoordinates] = useState('')
  const [mapTarget, setMapTarget] = useState<Position>()
  const [coordinateError, setCoordinateError] = useState(false)
  const mapCanvas = useRef<HTMLDivElement>(null)
  const generation = useRef(0)
  const lastPublished = useRef(JSON.stringify(geometry))
  const suggestion = useBoundarySuggestion()
  const locked = disabled || Boolean(busy)
  const showEditor = mode === 'manual' || (mode === 'review' && (editing || draft.append))
  const complete = reviewedBoundary(draft.points)
  const area = complete ? estimateHectares(draft.points) : 0
  const viewportRequest = useMemo(() => {
    if (mapTarget) return { key: 'coordinates', center: mapTarget, zoom: 16 }
    const selected = setupRegion({ countryCode, regionCode })
    return selected ? { key: countryCode + ':' + regionCode + ':' + recenter,
      bounds: selected.bounds, maxZoom: selected.maxZoom ?? 10 } : undefined
  }, [countryCode, regionCode, recenter, mapTarget])
  const countries = useMemo(() => [...setupCountries].sort((a, b) =>
    a[language].localeCompare(b[language], language)), [language])
  const regions = useMemo(() => setupRegions(countryCode).sort((a, b) =>
    a.code === 'all' ? -1 : b.code === 'all' ? 1 : a[language].localeCompare(b[language], language)), [countryCode, language])

  useEffect(() => () => { generation.current++ }, [])
  useEffect(() => {
    if (suggestion.search?.preview) setPreview(suggestion.search.preview)
  }, [suggestion.search?.preview])
  useEffect(() => {
    const next = JSON.stringify(geometry)
    if (next === lastPublished.current) return
    lastPublished.current = next
    setDraft(draftFrom(geometry))
    setEditing(false)
    setMode(geometry ? 'review' : 'automatic')
  }, [geometry])

  function publish(next: FieldGeometryDraft) {
    setDraft(next)
    const value = reviewedBoundary(next.points)
    lastPublished.current = JSON.stringify(value)
    onGeometryChange(value)
    setError(undefined)
  }

  function cancel() {
    generation.current++
    suggestion.close()
    setBusy(undefined)
  }

  function chooseLocation(next: SetupLocation) {
    if (countryCode === next.countryCode && regionCode === next.regionCode) return
    if ((!countryCode && (draft.points.length || mapTarget)) || (countryCode === next.countryCode && draft.points.length)) {
      if (!mapTarget && viewport) {
        const longitude = (viewport.west + viewport.east) / 2
        const latitude = (viewport.south + viewport.north) / 2
        setMapTarget([longitude, latitude])
      }
      onLocationChange(next)
      return
    }
    cancel()
    setPreview(undefined)
    setSource(undefined)
    setRetryPosition(undefined)
    setViewport(undefined)
    setCoordinates('')
    setMapTarget(undefined)
    setCoordinateError(false)
    setBasemap('aerial')
    setTileFailure(false)
    setMode('automatic')
    setEditing(false)
    publish(draftFrom(null))
    onLocationChange(next)
  }

  function adoptGeometry(value: Polygon, description: string, image?: SatelliteRaster) {
    const validated = polygonFrom(value.coordinates[0])
    if (!reviewedBoundary(validated.coordinates[0])) throw new Error(text('Перевірте межу: площа має бути до 100 000 га, а краї не мають перетинатися.', 'Check the boundary: area must be at most 100,000 ha and edges must not cross.'))
    publish({ ...fieldGeometryDraft(undefined, 'boundary'), points: validated.coordinates[0].slice(0, -1), append: false })
    setMode('review')
    setEditing(false)
    setSource(description)
    setRetryPosition(undefined)
    if (image) setPreview(image)
  }

  async function findBoundary(position: Position) {
    if (locked) return
    if (!viewport || viewport.east - viewport.west > .15 || viewport.north - viewport.south > .15) {
      setRetryPosition(undefined)
      setError(text('Наблизьте карту, щоб було видно окреме поле.', 'Zoom in until an individual field is visible.'))
      return
    }
    cancel()
    const operation = generation.current
    setError(undefined)
    setRetryPosition(position)
    setBusy('outline')
    try {
      const result = await suggestion.find(position, preview)
      if (operation !== generation.current) return
      if (result) {
        adoptGeometry(polygonFrom(result.points), result.source === 'model'
          ? ['Delineate Anything v2', result.imageryDate, result.imagerySource].filter(Boolean).join(' · ')
          : result.source === 'aerial'
            ? text('Оцінка контуру за аерознімком USGS', 'Outline estimated from USGS aerial imagery')
            : text('Оцінка контуру за знімком Sentinel-2', 'Outline estimated from Sentinel-2 imagery'), result.preview)
      }
    } catch {
      if (operation === generation.current) setError(text('Цю межу не вдалося використати. Обведіть поле вручну або спробуйте ще раз.',
        'This outline could not be used. Draw the field manually or try again.'))
    } finally {
      if (operation === generation.current) setBusy(undefined)
    }
  }

  async function showImagery() {
    if (!viewport || locked) return
    cancel()
    const operation = generation.current
    setBusy('imagery')
    setError(undefined)
    await suggestion.showSatellite([(viewport.west + viewport.east) / 2, (viewport.south + viewport.north) / 2])
    if (operation === generation.current) setBusy(undefined)
  }
  const displayedPreview = suggestion.search?.preview ?? preview

  function goToCoordinates() {
    const target = parseSetupCoordinates(coordinates)
    if (!target) {
      setCoordinateError(true)
      return
    }
    cancel()
    setError(undefined)
    setCoordinateError(false)
    setRetryPosition(undefined)
    setPreview(undefined)
    setMapTarget(target)
    mapCanvas.current?.scrollIntoView({ block: 'nearest' })
  }

  function manual() {
    cancel()
    setRetryPosition(undefined)
    setMode('manual')
    setSource(text('Межу позначено вручну', 'Manually drawn boundary'))
    setError(undefined)
    if (draft.points.length) publish({ ...draft, append: false })
  }

  function modify(points: Position[], append = draft.append, active?: number) {
    if (locked) return
    publish(recordShape(draft, { points, append, active }))
    if (!append && points.length >= 3) setMode('review')
  }

  return <section className="field-setup-map" aria-label={text('Вибір і перевірка поля', 'Choose and review your field')}>
    <div className="setup-location-controls">
      <LocationPicker label={text('Країна', 'Country')} placeholder={text('Введіть назву країни', 'Type a country')}
        noMatches={text('Країну не знайдено', 'No matching countries')}
        language={language} options={countries} value={countryCode} disabled={locked}
        onSelect={(code) => chooseLocation({ countryCode: code, regionCode: 'all' })} />
      <LocationPicker label={text('Область / регіон', 'Region')} placeholder={text('Введіть назву регіону', 'Type a region')}
        noMatches={text('Регіон не знайдено', 'No matching regions')}
        language={language} options={regions} value={regionCode} disabled={locked || !countryCode}
        onSelect={(code) => chooseLocation({ ...location, regionCode: code })} />
    </div>
    {mode === 'automatic' && <form className="setup-coordinate-search" onSubmit={(event) => { event.preventDefault(); goToCoordinates() }}>
      <label htmlFor="setup-coordinates">{text('Координати поля', 'Field coordinates')}</label>
      <div>
        <input id="setup-coordinates" placeholder="49.667, 34.557" value={coordinates}
          disabled={locked} aria-invalid={coordinateError} enterKeyHint="go"
          aria-describedby={coordinateError ? 'setup-coordinates-error' : undefined}
          onChange={(event) => { setCoordinates(event.target.value); setCoordinateError(false) }} />
        <button type="submit" className="button subtle" disabled={locked || !coordinates.trim()}>
          {text('На карті', 'Show on map')}
        </button>
      </div>
      {coordinateError && <small id="setup-coordinates-error" role="alert">{text(
        'Введіть широту й довготу, наприклад 49.667, 34.557.',
        'Enter latitude and longitude, for example 49.667, 34.557.',
      )}</small>}
    </form>}
      <div className="setup-map-toolbar">
        <p>{busy ? text(busy === 'imagery' ? 'Завантажую супутниковий знімок…' : 'Шукаю межу поля…', busy === 'imagery' ? 'Loading satellite imagery…' : 'Finding your field boundary…')
          : mode === 'automatic' ? text('Наблизьте карту й натисніть усередині вашого поля.', 'Zoom in and tap inside your field.')
            : draft.append ? text('Позначте кути поля на карті, потім завершіть контур.', 'Mark the corners, then finish the outline.')
              : showEditor
                ? text('Перетягніть точки, щоб виправити межу.', 'Drag the points to adjust the boundary.')
                : text('Перевірте, чи контур збігається з вашим полем.', 'Check that the outline matches your field.')}</p>
        <div>
          {countryCode && <button type="button" className="button subtle" disabled={locked}
            onClick={() => { setMapTarget(undefined); setRecenter((value) => value + 1) }}>
            <MapPinIcon aria-hidden="true" />{text('До регіону', 'Region')}
          </button>}
          <button type="button" className="button subtle"
            onClick={() => { setBasemap((value) => value === 'aerial' ? 'street' : 'aerial'); setTileFailure(false) }}>
            <PhotoIcon aria-hidden="true" />{basemap === 'aerial' ? text('Карта доріг', 'Street map') : text('Супутникова карта', 'Satellite map')}
          </button>
          <button type="button" className="button subtle" disabled={locked || !viewport || viewport.east - viewport.west > .15}
            onClick={() => void showImagery()}><PhotoIcon aria-hidden="true" />{text('Свіжий знімок', 'Recent imagery')}</button>
          {mode === 'automatic' && <button type="button" className="button subtle" disabled={disabled} onClick={manual}><PencilSquareIcon aria-hidden="true" />{text('Обвести вручну', 'Draw manually')}</button>}
          {mode === 'review' && !draft.append && <button type="button" className="button subtle" disabled={locked || (editing && !complete)}
            onClick={() => setEditing((value) => !value)}><PencilSquareIcon aria-hidden="true" />
            {editing ? text('Завершити редагування', 'Finish editing') : text('Редагувати межу', 'Edit boundary')}
          </button>}
          {mode !== 'automatic' && <button type="button" className="button subtle" disabled={locked} onClick={() => { cancel(); setMode('automatic'); setEditing(false); publish(draftFrom(null)); setSource(undefined) }}>{text('Обрати інше поле', 'Choose another field')}</button>}
        </div>
      </div>
      <div className="fields-content setup-map-canvas" ref={mapCanvas}>
        <Suspense fallback={<p className="setup-map-loading">{text('Завантажую карту…', 'Loading map…')}</p>}>
          <FieldMap fields={fields} showBoundaries points={draft.points} drawing={mode === 'manual' && draft.append}
            editMode={mode !== 'automatic' ? 'boundary' : undefined} editLocked={locked || !showEditor} appendVertices={draft.append}
            activeVertex={draft.active} selectVertex={(active) => setDraft((current) => ({ ...current, active }))}
            moveVertex={(index, point) => modify(draft.points.map((value, i) => i === index ? point : value))}
            insertVertex={(index, point) => { const points = [...draft.points]; points.splice(index, 0, point); modify(points, draft.append, index) }}
            newBoundary satellite={displayedPreview} autoFitFields={false} viewportRequest={viewportRequest}
            locationMarker={mode === 'automatic' && !suggestion.search?.seed ? mapTarget : undefined}
            suggestBoundary={mode === 'automatic' && !locked ? (position) => void findBoundary(position) : undefined}
            suggestionSeed={suggestion.search?.seed} basemap={basemap} retryTiles={retryTiles}
            chooseField={() => {}} addPoint={(point) => modify([...draft.points, point], true)}
            onBasemapFailure={(_failed, hasLocal) => setTileFailure(!hasLocal)}
            onBasemapReady={() => setTileFailure(false)}
            onViewport={setViewport} />
        </Suspense>
        {busy && <div className="setup-map-progress" role="status"><ArrowPathIcon className="spin" aria-hidden="true" />{text('Завантажую дані та шукаю межу. Карту можна рухати.', 'Loading imagery and finding the outline. You can still move the map.')}
          <button className="button subtle" type="button" onClick={cancel}>{text('Скасувати', 'Cancel')}</button></div>}
      </div>
      {tileFailure && <p className="setup-map-note" role="status">{text('Частина карти не завантажилась.', 'Some map tiles did not load.')} <button type="button" className="button subtle" onClick={() => setRetryTiles((value) => value + 1)}>{text('Повторити', 'Retry')}</button></p>}
      {(error || suggestion.search?.error) && <ErrorBox message={error ?? suggestion.search?.error ?? ''} retry={mode === 'automatic' && retryPosition && !locked
        ? () => void findBoundary(retryPosition) : undefined} />}
      {mode !== 'automatic' && source && <p className="setup-map-note">{source} · {text('Перевірте за актуальним знімком', 'Review against current imagery')}</p>}
      {showEditor && <div className="setup-boundary-review">
        <FieldGeometryEditor draft={draft} season={season} saving={locked}
          onCoverage={() => {}} onSelect={(active) => setDraft((current) => ({ ...current, active }))}
          onReplace={(points, append) => modify(points, append)} onUndo={() => publish(undoShape(draft))} />
        {draft.points.length >= 3 && !complete && <p className="setup-map-error" role="alert">{text('Перевірте точки: краї не мають перетинатися, а площа має бути до 100 000 га.', 'Check the points: edges must not cross and area must be at most 100,000 ha.')}</p>}
      </div>}
      <div className="setup-map-confirm"><p>{complete ? <><strong>{number(area, 2)} {text('га', 'ha')}</strong><span>{countryCode
        ? text('Площа за обраною межею', 'Area within your outline')
        : text('Оберіть країну, щоб підтвердити межу.', 'Choose a country to confirm the outline.')}</span></>
        : text('Підтвердьте межу, щоб перейти до питань про поле.', 'Confirm a boundary to continue to the field questions.')}</p>
        <button type="button" className="button primary" disabled={locked || !countryCode || !complete || mode === 'automatic' || draft.append}
          onClick={onConfirm}><CheckIcon aria-hidden="true" />{confirmLabel ?? text('Так, це моє поле', 'Yes, this is my field')}</button>
      </div>
  </section>
}
