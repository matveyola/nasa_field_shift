import { cropInput, useLocale } from '../../shared/locale'
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { AdjustmentsHorizontalIcon, ArrowPathIcon, ChartBarIcon, ChevronRightIcon, MapIcon, PencilSquareIcon, PlusIcon, ViewfinderCircleIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { api, ApiError } from '../../api/client'
import type { Field, MoistureLayer, MoistureSource, Position } from '../../api/contracts'
import type { AnalyticsSection, MapFocus } from '../../shared/productContext'
import { JobStatus } from '../../shared/JobStatus'
import { ErrorBox, PageHeading } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import { FieldContextPanel } from './FieldContextPanel'
import { FieldPanelResizer } from './FieldPanelResizer'
import type { BasemapId } from './basemaps'
import { FieldGeometryEditor } from './FieldGeometryEditor'
import { fieldGeometryDraft, recordShape, undoShape, type FieldEditMode, type FieldGeometryDraft } from './fieldGeometryDraft'
import { estimateHectares, moistureColor, parseVertices, polygonFrom } from './geometry'
import { readGeoJsonBoundary } from './geoJsonBoundary'
import { MapLayerPicker } from './MapLayerPicker'
import { initialVisualization, visibleVisualization, type FieldMapVisualization, type VisualizationId } from './fieldMapVisualization'
import { MoistureForecastPanel, useMoistureForecast } from './MoistureForecastPanel'
import { useSatelliteLayer } from './satellite'
import { useBoundarySuggestion } from './useBoundarySuggestion'
import { useMoistureOverlay, type MoistureOverlay } from './useMoistureOverlay'
import { useFieldAnalytics } from './useFieldAnalytics'
import { MapObservationControls } from './MapObservationControls'
import { spectralCell, spectralColorStops } from './spectralIndices'
import { BoundaryHistoryPanel } from './BoundaryHistoryPanel'
import { PreparedContoursPanel, type MapWindow } from './PreparedContoursPanel'
import type { PreparedContour } from '../../api/boundaries'
import { useAccess } from '../access/AccessProvider'
import './map.css'
import './fieldWorkspace.css'
import './fieldMapLegend.css'

const FieldMap = lazy(() => import('./FieldMap'))

const moistureSources = {
  historical: 'Historical soil moisture', ml: 'Modelled soil moisture', demo: 'Demo soil moisture',
} satisfies Record<MoistureSource, string>

type Props = {
  fields: Field[]
  selected?: Field
  season: number
  initialDrawing?: boolean
  initialAnalysis?: boolean
  showNdviLayer?: boolean
  showTerrainLayer?: boolean
  mapFocus?: MapFocus
  openStrategies?: () => void
  openAnalytics?: (section?: AnalyticsSection) => void
  openAgent?: () => void
  chooseField: (id: string) => void
  onCreate: (field: Field) => void
  onUpdate: (field: Field) => void
  reloadFields: () => void
  openForecast: (fieldId: string, day?: string) => void
  onSetup?: () => void
  setupLabel?: string
}

export function FieldsPage({ fields, selected, season, initialDrawing = false, initialAnalysis, showNdviLayer, showTerrainLayer, mapFocus, openStrategies, openAnalytics, openAgent, chooseField, onCreate, onUpdate, reloadFields, openForecast, onSetup, setupLabel }: Props) {
  const { t, number, date, shortDate, language } = useLocale()
  const { canEdit } = useAccess()
  const [preparedContours, setPreparedContours] = useState<PreparedContour[]>([])
  const [contourFocus, setContourFocus] = useState<Position>()
  const [viewport, setViewport] = useState<MapWindow>()
  const linkedVisualization = showTerrainLayer ? 'terrain' : initialVisualization(showNdviLayer)
  const [selection, setSelection] = useState<{ link: VisualizationId; active: VisualizationId }>(() => ({ link: linkedVisualization, active: linkedVisualization }))
  const [showBoundaries, setShowBoundaries] = useState(true)
  const requestedVisualization = selection.link === linkedVisualization ? selection.active : linkedVisualization
  if (selection.link !== linkedVisualization) setSelection({ link: linkedVisualization, active: requestedVisualization })
  const [drawing, setDrawing] = useState(initialDrawing && canEdit)
  const [points, setPoints] = useState<Position[]>([])
  const [draft, setDraft] = useState<FieldGeometryDraft>()
  useEffect(() => { if (!canEdit) { setDrawing(false); setDraft(undefined) } }, [canEdit])
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<{ message: string; needsReload: boolean }>()
  const [savedEdit, setSavedEdit] = useState<{ fieldId: string; mode: FieldEditMode }>()
  const mapHeading = useRef<HTMLHeadingElement>(null)
  const mapFrame = useRef<HTMLDivElement>(null)
  const [sheetOcclusion, setSheetOcclusion] = useState({ fieldId: '', height: 0 })
  const onSheetPosition = useCallback((fieldId: string, top?: number) => {
    const height = top === undefined || !mapFrame.current ? 0 : Math.max(0, mapFrame.current.getBoundingClientRect().bottom - top)
    setSheetOcclusion((previous) => previous.fieldId === fieldId && previous.height === height ? previous : { fieldId, height })
  }, [])
  const [preferredBasemap, setPreferredBasemap] = useState<BasemapId>()
  const [tileFailure, setTileFailure] = useState(false)
  const [localAerial, setLocalAerial] = useState(false)
  const [retryTiles, setRetryTiles] = useState(0)
  const [satelliteDate, setSatelliteDate] = useState('')
  const [showSurfaceForecast, setShowSurfaceForecast] = useState(false)
  const surfaceForecastPanel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (showSurfaceForecast) {
      surfaceForecastPanel.current?.scrollIntoView({ block: 'start' })
      surfaceForecastPanel.current?.focus({ preventScroll: true })
    }
  }, [showSurfaceForecast])
  const [suggestionCenter, setSuggestionCenter] = useState<Position>()
  const suggestion = useBoundarySuggestion()
  const search = suggestion.search
  const selectedId = selected?.id
  const boundaryRevision = selected?.boundary_revision ?? 0
  const fieldIndex = fields.findIndex(({ id }) => id === selectedId)
  const edit = draft && (!draft.fieldId || draft.fieldId === selectedId) ? draft : undefined
  const newBoundary = Boolean(edit && !edit.fieldId)
  const editMode = edit?.mode
  useEffect(() => {
    if (editMode) mapHeading.current?.focus()
  }, [editMode])
  const drawingMap = drawing || Boolean(edit)
  const inspecting = !drawingMap && !search
  const assistantField = inspecting ? selected : undefined
  const activeVisualization = visibleVisualization(requestedVisualization, Boolean(assistantField), Boolean(assistantField?.planting))
  const analysisSeason = season
  const analytics = useFieldAnalytics(assistantField, analysisSeason, true, mapFocus)
  const sceneLayer = analytics.activeScene.data?.heterogeneity
  const comparingNdvi = Boolean(analytics.comparison && !analytics.spectralIndex)
  const vegetationIndex = analytics.spectralIndex ?? (comparingNdvi ? 'ndvi' : 'relative')
  const [fieldContrast, setFieldContrast] = useState(false)
  const vegetationColors = useMemo(() => {
    if (vegetationIndex === 'relative') return undefined
    const values = fieldContrast && !comparingNdvi
      ? sceneLayer?.cells.flatMap((cell) => {
        const value = spectralCell(cell, vegetationIndex)
        return value == null ? [] : [value]
      }) : undefined
    return spectralColorStops(vegetationIndex, values)
  }, [vegetationIndex, sceneLayer, fieldContrast, comparingNdvi])
  const sceneHasLayer = sceneLayer && (vegetationIndex === 'relative' ? sceneLayer.cells.length > 0
    : sceneLayer.cells.some((cell) => Number.isFinite(spectralCell(cell, vegetationIndex))))
  const sceneBoundary = activeVisualization === 'heterogeneity' ? analytics.vegetation.data?.boundary_geometry : undefined
  const mapFields = useMemo(() => sceneBoundary
    ? fields.map((field) => field.id === selectedId
      ? { ...field, geometry: sceneBoundary, planting: analysisSeason === season ? field.planting : null }
      : field)
    : fields, [fields, selectedId, sceneBoundary, analysisSeason, season])
  const mapSelected = mapFields.find((field) => field.id === selectedId)
  const forecastState = useMoistureForecast(inspecting ? selectedId : undefined, boundaryRevision)
  const showForecast = activeVisualization === 'forecast'
  const currentSurface = forecastState.days.find((day) => day.date === forecastState.today)
  const satelliteActive = activeVisualization === 'satellite'
  const satellite = useSatelliteLayer(selectedId, satelliteActive, satelliteDate, boundaryRevision)
  const scene = satellite.data?.scene
  const overlayField = (source: MoistureSource) => activeVisualization === source ? selectedId : undefined
  const historical = useMoistureOverlay('historical', overlayField('historical'), season, boundaryRevision)
  const model = useMoistureOverlay('ml', overlayField('ml'), season, boundaryRevision)
  const demo = useMoistureOverlay('demo', overlayField('demo'), season, boundaryRevision)
  const moistureOverlay = [historical, model, demo].find(({ source }) => activeVisualization === source)
  const moistureLayer = moistureOverlay?.layer
  const terrainResult = analytics.terrain.data
  const satelliteRaster = satellite.data?.raster
  const vegetationGeometry = sceneBoundary ?? selected?.geometry
  const visualization = useMemo<FieldMapVisualization | undefined>(() => {
    if (activeVisualization === 'heterogeneity' && sceneLayer && sceneHasLayer && vegetationGeometry) {
      return { kind: 'vegetation', layer: sceneLayer, index: vegetationIndex, geometry: vegetationGeometry, colorStops: vegetationColors }
    }
    if (activeVisualization === 'terrain' && terrainResult?.status === 'ready' && terrainResult.vectors.length) {
      return { kind: 'terrain', result: terrainResult }
    }
    if (activeVisualization === 'satellite' && satelliteRaster) return { kind: 'satellite', raster: satelliteRaster }
    if (activeVisualization === 'planting') return { kind: 'planting' }
    if (moistureLayer?.cells.length) return { kind: 'moisture', layer: moistureLayer }
  }, [activeVisualization, sceneHasLayer, sceneLayer, vegetationIndex, vegetationColors, vegetationGeometry, terrainResult, satelliteRaster, moistureLayer])
  const hasSettings = satelliteActive || moistureOverlay?.canRefresh
  const basemap = preferredBasemap ?? 'aerial'
  const mapLayers: Array<{ id: VisualizationId; label: string }> = [historical, model, demo]
    .map(({ source: id, layer }) => {
      const layerDepth = layer ? [...new Set(layer.depth_cm)].join('–') + ' ' + t('cm') : undefined
      const label = layer?.source?.kind === 'ml'
        ? layer.source.experimental ? 'Experimental ML' : 'ML surface moisture' : moistureSources[id]
      return { id, label: t(label) + (layerDepth ? ' · ' + layerDepth : '') }
    })
  mapLayers.unshift({ id: 'forecast', label: t('Today’s soil moisture · IFS') })
  const vegetationLabel = analytics.spectralIndex?.toUpperCase() ?? (comparingNdvi ? t('NDVI comparison') : t('Within-field variation'))
  mapLayers.push({ id: 'heterogeneity', label: vegetationLabel })
  mapLayers.push({ id: 'terrain', label: t('Terrain directions · 30 m') })
  if (selected?.planting) mapLayers.push({ id: 'planting', label: t('Planted area') })
  mapLayers.push({ id: 'satellite', label: t('Satellite image by date') })

  function startEdit(mode: FieldEditMode) {
    if (!selected || !canEdit) return
    setDraft(fieldGeometryDraft(selected, mode))
    setEditError(undefined)
    setSavedEdit(undefined)
  }

  function changeDraft(change: (current: FieldGeometryDraft) => FieldGeometryDraft) {
    if (!edit || saving || !canEdit) return
    setDraft((current) => current && current.fieldId === edit.fieldId ? change(current) : current)
    setEditError(undefined)
  }

  function addPoint(point: Position) {
    if (!canEdit) return
    if (edit) {
      changeDraft((current) => current.fullField || !current.append ? current : recordShape(current, {
        points: [...current.points, point], append: true, active: current.points.length,
      }))
    } else {
      setPoints((value) => [...value, point])
    }
  }

  function updateVertex(index: number, point: Position, insert = false) {
    changeDraft((current) => {
      const vertices = [...current.points]
      vertices.splice(index, insert ? 0 : 1, point)
      return recordShape(current, { points: vertices, append: current.append, active: index })
    })
  }

  async function saveEditedArea() {
    if (!edit?.fieldId || saving || !canEdit) return
    setEditError(undefined)
    setSaving(true)
    try {
      const updated = edit.mode === 'boundary'
        ? await api.saveBoundary(edit.fieldId, { geometry: polygonFrom(edit.points), boundary_revision: edit.revision, effective_season: edit.effectiveSeason })
        : await api.savePlanting(edit.fieldId, season, {
          geometry: edit.fullField ? null : polygonFrom(edit.points), boundary_revision: edit.revision,
        })
      onUpdate(updated)
      if (edit.mode === 'planting') selectVisualization('planting')
      setSavedEdit({ fieldId: updated.id, mode: edit.mode })
      setDraft(undefined)
    } catch (failure) {
      const message = errorText(failure).replace(/^Value error, /, '')
      setEditError({ message, needsReload: failure instanceof ApiError && failure.status === 409
        && message === 'The field boundary changed. Reload the field before saving.' })
    } finally {
      setSaving(false)
    }
  }

  async function suggestBoundary(position: Position, fieldId?: string) {
    const result = await suggestion.find(position, satelliteActive ? satellite.data?.raster : undefined)
    if (!result) return
    const field = fields.find(({ id }) => id === fieldId)
    if (field && field.id !== selectedId) chooseField(field.id)
    setSavedEdit(undefined)
    setEditError(undefined)
    setDraft({
      ...fieldGeometryDraft(field, 'boundary'),
      points: result.points,
      append: false,
      suggestion: { source: result.source, preview: result.preview,
        imageryDate: result.imageryDate, imagerySource: result.imagerySource },
    })
  }

  function chooseSuggestionTarget(fieldId?: string) {
    const field = fields.find(({ id }) => id === fieldId)
    if (field && field.id !== selectedId) chooseField(field.id)
    changeDraft((current) => ({ ...current, fieldId: field?.id, revision: field?.boundary_revision ?? 0 }))
  }

  function stepField(direction: -1 | 1) {
    if (fieldIndex < 0 || fields.length < 2) return
    chooseField(fields[(fieldIndex + direction + fields.length) % fields.length].id)
  }

  function selectBasemap(id: BasemapId) {
    setPreferredBasemap(id)
    setTileFailure(false)
    setLocalAerial(false)
    setRetryTiles((value) => value + 1)
  }

  function onBasemapFailure(failed: BasemapId, hasLocalAerial: boolean) {
    const savedAerialAvailable = failed === 'aerial' && hasLocalAerial
    setLocalAerial(savedAerialAvailable)
    setTileFailure(!savedAerialAvailable)
  }

  function selectVisualization(active: VisualizationId) {
    setSelection({ link: linkedVisualization, active })
  }

  function revealNdviLayer() {
    selectVisualization('heterogeneity')
  }

  return (
    <div className="page-body fields-page">
      <PageHeading title="Fields" season={season} dataLabel={null} />
      <div className="fields-content">
        {onSetup && <div className="field-setup-banner">
          <button type="button" className="button primary" onClick={onSetup}>
            <PlusIcon aria-hidden="true" />{setupLabel ?? t('Add field')}
          </button>
        </div>}
        <div className={'fields-layout' + (assistantField ? '' : ' is-editing')}>
          {assistantField && <FieldContextPanel field={assistantField}
            season={season} openStrategies={openStrategies} openAnalytics={openAnalytics} openAgent={openAgent}
            initialAnalysis={initialAnalysis} mapFirst={showNdviLayer} vegetation={analytics.vegetation.data}
            analysis={() => <MapObservationControls state={analytics} reveal={revealNdviLayer}
              openAnalytics={openAnalytics ? () => openAnalytics('vegetation') : undefined} />}
            canSwitch={fields.length > 1} onStep={stepField} openForecast={openForecast} surfaceForecastOpen={showSurfaceForecast}
            onSheetPosition={onSheetPosition}
            surface={currentSurface && forecastState.forecast ? { value: currentSurface.ifs_value, date: currentSurface.date, provider: forecastState.forecast.provider } : undefined}>
            <BoundaryHistoryPanel key={assistantField.id} field={assistantField} season={season} onEdit={(geometry, effectiveSeason) => {
              setDraft({ ...fieldGeometryDraft(assistantField, 'boundary'), points: geometry.coordinates[0].slice(0, -1), effectiveSeason })
              setEditError(undefined); setSavedEdit(undefined)
            }} />
            <PreparedContoursPanel viewport={viewport} onContours={setPreparedContours} onFocus={setContourFocus} />
            <details className="field-context-details">
              <summary><MapIcon aria-hidden="true" /><span>{t('Boundaries and planting')}</span><ChevronRightIcon aria-hidden="true" /></summary>
              <div className="field-area-strip">
                <div className="field-planting-summary">
                  <strong>{t('Planted area')}</strong>
                  <span>{assistantField.planting
                    ? number(assistantField.planting.area_ha, 2) + ' ' + t('ha')
                    : t('Entire field assumed')}</span>
                </div>
                <div className="field-editor-actions">
                  <button type="button" className="button subtle" disabled={saving || !canEdit} onClick={() => startEdit('boundary')}>
                    <PencilSquareIcon aria-hidden="true" />{t('Edit boundary')}
                  </button>
                  <button type="button" className="button subtle" disabled={saving || !canEdit} onClick={() => startEdit('planting')}>
                    {t('Edit planted area')}
                  </button>
                </div>
                {savedEdit?.fieldId === assistantField.id && <small className="field-area-saved" role="status">
                  {t(savedEdit.mode === 'boundary' ? 'Boundary saved.' : 'Planted area saved.')}
                </small>}
              </div>
              <div className="field-management-actions">
                <button type="button" className="button subtle" disabled={!canEdit} onClick={() => {
                  if (basemap !== 'aerial') selectBasemap('aerial')
                  suggestion.open()
                }}><ViewfinderCircleIcon aria-hidden="true" />{t('Find boundary')}</button>
                <button type="button" className="button subtle" disabled={!canEdit} onClick={onSetup ?? (() => { setDrawing(true); setPoints([]) })}>
                  <PlusIcon aria-hidden="true" />{t('Add field')}
                </button>
              </div>
            </details>
            <details className="field-context-details">
              <summary><AdjustmentsHorizontalIcon aria-hidden="true" /><span>{t('Layer data and settings')}</span><ChevronRightIcon aria-hidden="true" /></summary>
              <div className="map-inspector">
              <div className="map-details">
                {scene && <div className="map-legend regional-legend satellite-scene">
                  <small>{number(scene.tile_cloud_percent, 1)}{t('% tile cloud cover')}</small>
                  <small className="moisture-detail">{t('Contains modified Copernicus Sentinel data')} {scene.acquired_at.slice(0, 4)} ·{' '}
                    <a href={scene.catalog_url} target="_blank" rel="noreferrer">{t('Earth Search scene')}</a></small>
                </div>}
                {moistureLayer?.cells.length && !moistureLayer.demo ? <MoistureLegend layer={moistureLayer} stale={Boolean(moistureOverlay?.stale)} /> : null}
                {tileFailure && <p className="map-fallback" role="status">{t('Basemap tiles unavailable. Field boundaries remain visible.')}
                  <button type="button" className="button subtle" onClick={() => { setTileFailure(false); setRetryTiles((value) => value + 1) }}>{t('Retry')}</button>
                </p>}
                {localAerial && <p className="map-fallback" role="status">{t('Live tiles unavailable. Showing a saved USGS aerial image of this area.')}</p>}
              </div>
              {hasSettings && <div id="field-map-settings" className="map-settings-panel">
                {satelliteActive && <label className="control satellite-date">
                  <span>{t('Satellite on or before')}</span>
                  <input type="date" value={satelliteDate} min="2015-06-23" max={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => setSatelliteDate(event.target.value)} />
                </label>}
                {moistureOverlay?.canRefresh && <MoistureControls overlay={moistureOverlay} />}
              </div>}
              </div>
            </details>
            <button type="button" className="button subtle field-surface-toggle" aria-expanded={showSurfaceForecast}
              aria-controls="field-surface-forecast" onClick={() => setShowSurfaceForecast(!showSurfaceForecast)}>
              <ChartBarIcon aria-hidden="true" />{t('Soil moisture forecast')}
            </button>
          </FieldContextPanel>}
          {assistantField && <FieldPanelResizer />}
          <section className="field-map-card" aria-label={t('Field map')}>
            {!assistantField && <header className="field-map-heading">
              <h2 id="field-map-heading" ref={mapHeading} tabIndex={-1}>
                {t(newBoundary ? 'Review suggested boundary'
                  : edit ? (edit.mode === 'boundary' ? 'Edit boundary' : 'Edit planted area') : 'Field map')}
              </h2>
              <div className="field-editor-actions">
                {inspecting && <button type="button" className="button subtle" disabled={!canEdit} onClick={() => {
                  if (basemap !== 'aerial') selectBasemap('aerial')
                  suggestion.open()
                }}>
                  <ViewfinderCircleIcon aria-hidden="true" />{t('Find boundary')}
                </button>}
                <button type="button" className="button subtle" disabled={saving || !canEdit} onClick={() => {
                  if (search) suggestion.close()
                  else if (edit) {
                    setDraft(undefined)
                    setEditError(undefined)
                  } else if (!drawing && onSetup) onSetup()
                  else {
                    setDrawing(!drawing)
                    setPoints([])
                  }
                }}>
                  {drawingMap || search ? <XMarkIcon aria-hidden="true" /> : <PlusIcon aria-hidden="true" />}
                  {t(search ? 'Cancel boundary search' : edit ? 'Cancel editing' : drawing ? 'Cancel drawing' : 'Add field')}
                </button>
              </div>
            </header>}
            {search && <div className="field-search-status">
              <p role="status">{t(search.pending === 'imagery' ? 'Loading satellite imagery…'
                : search.pending === 'boundary' ? 'Finding the field boundary…'
                  : search.error ?? 'Click inside a field on the image.')}</p>
              {basemap === 'street' && <button type="button" className="button subtle"
                disabled={!suggestionCenter || Boolean(search.pending)}
                onClick={() => { if (suggestionCenter) void suggestion.showSatellite(suggestionCenter) }}>
                {t('Show satellite here')}
              </button>}
            </div>}
            <div className="map-frame" ref={mapFrame}>
              <Suspense fallback={<div className="map-loading" role="status">{t('Loading geographic map…')}</div>}>
                <FieldMap fields={mapFields} selected={mapSelected} visualization={visualization} showBoundaries={showBoundaries}
                  bottomOcclusion={sheetOcclusion.fieldId === selectedId ? sheetOcclusion.height : 0}
                  satellite={edit?.suggestion?.preview ?? search?.preview}
                  newBoundary={newBoundary} suggestionSeed={search?.seed}
                  suggestBoundary={search && !search.pending ? suggestBoundary : undefined}
                  reportSuggestionCenter={setSuggestionCenter}
                  points={edit ? (edit.fullField ? [] : edit.points) : points} drawing={drawingMap}
                  editMode={editMode} editLocked={saving} appendVertices={!edit || (!edit.fullField && edit.append && !saving)}
                  activeVertex={edit?.active} selectVertex={(active) => changeDraft((current) => ({ ...current, active }))}
                  moveVertex={updateVertex} insertVertex={(index, point) => updateVertex(index, point, true)}
                  basemap={basemap} retryTiles={retryTiles} onBasemapFailure={onBasemapFailure}
                  onBasemapReady={() => { setTileFailure(false); setLocalAerial(false) }}
                  preparedContours={preparedContours} contourFocus={contourFocus} onViewport={setViewport}
                  adoptContour={canEdit ? (contour) => {
                    setDraft({ ...fieldGeometryDraft(undefined, 'boundary'), points: contour.geometry.coordinates[0].slice(0, -1), append: false,
                      catalogue: { year: contour.year, source: contour.source, sourceUrl: contour.source_url } })
                    setEditError(undefined); setSavedEdit(undefined)
                  } : undefined}
                  chooseField={chooseField} addPoint={addPoint} />
              </Suspense>
              <div className="map-tools">
                <MapLayerPicker basemap={basemap}
                  layers={mapLayers} activeVisualization={activeVisualization} fieldAvailable={Boolean(assistantField)}
                  showBoundaries={showBoundaries} onBoundaries={setShowBoundaries}
                  onBasemap={selectBasemap} onVisualization={selectVisualization} />
              </div>
              {activeVisualization !== 'none' && <div className="map-analytics-legend field-map-legend">
                <strong>{mapLayers.find(({ id }) => id === activeVisualization)?.label}</strong>
                {showForecast && <CurrentMoisture state={forecastState} />}
                {moistureOverlay && <MoistureOverlayLegend overlay={moistureOverlay} />}
                {satelliteActive && <div role="status">
                  <small>{scene ? <><time dateTime={scene.acquired_at}>{date(scene.acquired_at)}</time> · Sentinel-2 · 10 {t('m')}</>
                    : t(satellite.error ?? (satellite.pending ? 'Loading Sentinel-2 image…' : 'No suitable Sentinel-2 scene in the preceding 35 days.'))}</small>
                  {satellite.error && <button className="ndvi-retry" type="button" onClick={satellite.reload}>{t('Retry')}</button>}
                </div>}
                {activeVisualization === 'planting' && assistantField?.planting &&
                  <small>{assistantField.planting.season} · {number(assistantField.planting.area_ha, 2)} {t('ha')}</small>}
                {activeVisualization === 'heterogeneity' && <>
                  {comparingNdvi && analytics.comparison && analytics.primary && <div className="ndvi-map-switch" role="group" aria-label={t('Select comparison map')}>
                    <button type="button" aria-pressed={!analytics.showingComparison} onClick={() => analytics.showComparison(false)}>
                      A · {shortDate(analytics.primary.date)}
                    </button>
                    <button type="button" aria-pressed={analytics.showingComparison} onClick={() => analytics.showComparison(true)}>
                      B · {shortDate(analytics.comparison.date)}
                    </button>
                  </div>}
                  <div role="status">
                    {sceneHasLayer ? <>
                      {vegetationIndex !== 'relative'
                        ? <>{!comparingNdvi && <div className="field-map-contrast" role="group"
                            aria-label={language === 'uk' ? 'Шкала індексу' : 'Index scale'}>
                            <button type="button" aria-pressed={!fieldContrast} onClick={() => setFieldContrast(false)}>
                              {language === 'uk' ? 'Стандартна' : 'Standard'}
                            </button>
                            <button type="button" aria-pressed={fieldContrast} onClick={() => setFieldContrast(true)}>
                              {language === 'uk' ? 'Контраст поля' : 'Field contrast'}
                            </button>
                          </div>}
                          <MapColorScale stops={vegetationColors!} />
                          <small>{language === 'uk'
                            ? (fieldContrast && !comparingNdvi ? 'Мін.–макс. цього поля · безрозмірний індекс' : 'Безрозмірний індекс')
                            : (fieldContrast && !comparingNdvi ? 'Field min–max · unitless index' : 'Unitless index')}</small>
                          <DisplaySmoothingNote /></>
                        : <div className="heterogeneity-legend"><span><i className="zone-low" />{t('Lower')}</span><span><i className="zone-typical" />{t('Typical')}</span><span><i className="zone-high" />{t('Higher')}</span></div>}
                      <small><time dateTime={sceneLayer.date}>{date(sceneLayer.date)}</time> · Sentinel-2 · {sceneLayer.resolution_m} {t('m')}</small>
                    </> : <small>
                      {analytics.activeScene.error || analytics.vegetation.error
                        ? (language === 'uk' ? 'Не вдалося завантажити супутникові дані.' : 'Could not load satellite data.')
                        : analytics.activeScene.pending || analytics.vegetation.pending
                          ? (language === 'uk' ? 'Завантаження супутникових даних…' : 'Loading satellite data…')
                          : (language === 'uk' ? 'Для цього спостереження немає карти.' : 'No map for this observation.')}
                    </small>}
                  </div>
                  {(analytics.activeScene.error || analytics.vegetation.error) && <button className="ndvi-retry" type="button"
                    onClick={analytics.vegetation.error ? analytics.vegetation.reload : analytics.activeScene.reload}>{t('Retry')}</button>}
                </>}
                {activeVisualization === 'terrain' && <div role="status">
                  <small>{terrainResult?.status === 'ready' && terrainResult.vectors.length
                    ? <><time dateTime={terrainResult.generated_at}>{date(terrainResult.generated_at)}</time> · {terrainResult.source} · {terrainResult.source_resolution_m} {t('m')}</>
                    : analytics.terrain.error ? (language === 'uk' ? 'Не вдалося завантажити дані рельєфу.' : 'Could not load terrain data.')
                      : analytics.terrain.pending || analytics.terrainPending ? (language === 'uk' ? 'Завантаження даних рельєфу…' : 'Loading terrain data…')
                        : terrainResult?.status === 'ready' ? (language === 'uk' ? 'За цієї роздільності напрям схилу не визначено.' : 'No reliable downhill direction at this resolution.')
                          : (language === 'uk' ? 'Для цього поля немає збережених даних рельєфу.' : 'No saved terrain data for this field.')}</small>
                  {analytics.terrain.error && <button className="ndvi-retry" type="button" onClick={analytics.terrain.reload}>{t('Retry')}</button>}
                </div>}
              </div>}
            </div>
            {edit && (
              <FieldGeometryEditor key={edit.suggestion ? 'suggested' : edit.fieldId + ':' + edit.mode}
                draft={edit} field={edit.fieldId ? selected : undefined} season={season}
                saving={saving} error={editError?.message} onSave={edit.fieldId ? () => void saveEditedArea() : undefined}
                onReload={editError?.needsReload ? reloadFields : undefined}
                suggestionTargets={fields} onSuggestionTarget={chooseSuggestionTarget}
                onCoverage={(fullField) => changeDraft((current) => ({ ...current, fullField }))}
                onSelect={(active) => changeDraft((current) => ({ ...current, active }))}
                onReplace={(vertices, append) => changeDraft((current) => recordShape(current, { points: vertices, append }))}
                onUndo={() => changeDraft(undoShape)} />
            )}
          </section>
        </div>
        {moistureOverlay && (moistureOverlay.submitError || moistureOverlay.jobId) && (
          <div className="map-job-status" aria-label={t(moistureSources[moistureOverlay.source])}>
            {moistureOverlay.submitError && <ErrorBox message={moistureOverlay.submitError} />}
            {moistureOverlay.jobId && <JobStatus job={moistureOverlay.job} error={moistureOverlay.jobError} />}
          </div>
        )}
        {selected && inspecting && showSurfaceForecast && <div id="field-surface-forecast" className="field-surface-forecast" ref={surfaceForecastPanel} tabIndex={-1}>
          <MoistureForecastPanel key={selected.id} state={forecastState} />
        </div>}
        {canEdit && drawing && <NewFieldForm season={season} points={points} onImport={setPoints} undoPoint={() => setPoints((value) => value.slice(0, -1))}
          onSaving={setSaving} onCreate={(field) => { setDrawing(false); setPoints([]); onCreate(field) }} />}
        {canEdit && newBoundary && edit && <NewFieldForm season={season} points={edit.points} assisted onSaving={setSaving}
          onCreate={(field) => { setDraft(undefined); onCreate(field) }} />}
      </div>
    </div>
  )
}

function CurrentMoisture({ state }: { state: ReturnType<typeof useMoistureForecast> }) {
  const { date, number, t } = useLocale()
  const { forecast, days, today, saved } = state
  const current = days.find((day) => day.date === today)
  return (
    <div role="status">
      {forecast && current ? (
        <>
          <small>{number(current.ifs_value, 3)} {forecast.unit} · <time dateTime={current.date}>{date(current.date)}</time></small>
          <small>{t('Daily UTC estimate · 0–7 cm')} · {forecast.provider} · {number(forecast.resolution_km)} {t('km')}</small>
        </>
      ) : <small>{t(saved.error ?? (saved.pending ? 'Loading forecast…' : 'No forecast available for today.'))}</small>}
      {saved.error && <button className="ndvi-retry" type="button" onClick={saved.reload}>{t('Retry')}</button>}
    </div>
  )
}

function MoistureOverlayLegend({ overlay }: { overlay: MoistureOverlay }) {
  const { t, number, date } = useLocale()
  const { layer, resource, pending, sampleDate } = overlay
  return (
    <div role="status">
      {layer?.cells.length ? <>
        {layer.demo ? <>
          <MapColorScale stops={Array.from({ length: 4 }, (_, index) => {
            const value = layer.minimum + (layer.maximum - layer.minimum) * index / 3
            return { value, color: moistureColor(value, layer.minimum, layer.maximum) }
          })} unit={layer.unit} />
          <DisplaySmoothingNote />
        </> : <small>{number(layer.cells[0].value, 3)} {layer.unit}</small>}
        <small><time dateTime={layer.date}>{date(layer.date)}</time> · {layer.demo ? t('Simulated preview') : layer.source?.name}</small>
        {layer.source && <small>{t('Grid spacing')} {number(layer.source.resolution_degrees, 3)}°</small>}
      </> : (
        <small>
          {resource.error ? t(resource.error)
            : resource.pending || pending ? t('Loading moisture…')
              : sampleDate ? t('No saved estimate for this date.') : t('No saved moisture estimate yet.')}
        </small>
      )}
      {resource.error && <button className="ndvi-retry" type="button" onClick={resource.reload}>{t('Retry')}</button>}
    </div>
  )
}

function MapColorScale({ stops, unit }: { stops: { value: number; color: string }[]; unit?: string }) {
  const { number } = useLocale()
  const min = stops[0].value
  const max = stops[stops.length - 1].value
  const gradient = min === max ? stops[0].color
    : 'linear-gradient(90deg, ' + stops.map(({ value, color }) => color + ' ' + 100 * (value - min) / (max - min) + '%').join(', ') + ')'
  return (
    <div className="field-map-scale">
      <i style={{ background: gradient }} aria-hidden="true" />
      <div>
        <span>{number(min, 4)}{min === max && unit ? ' ' + unit : ''}</span>
        {min !== max && <span>{number(max, 4)}{unit ? ' ' + unit : ''}</span>}
      </div>
    </div>
  )
}

function DisplaySmoothingNote() {
  const { language } = useLocale()
  return <small>{language === 'uk'
    ? 'Плавні кольори не змінюють просторову роздільність даних.'
    : 'Colors are smoothed for display. The source resolution is unchanged.'}</small>
}

function MoistureControls({ overlay }: { overlay: MoistureOverlay }) {
  const { t } = useLocale()
  const { canEdit } = useAccess()
  const helpId = useId()
  const { source, refresh, sampleDate, setSampleDate, archiveDate, submitting, pending } = overlay
  return (
    <form className="moisture-request" aria-label={t(moistureSources[source])} onSubmit={(event) => void refresh(event)}>
      <label className="control"><span>{t(source === 'ml' ? 'Model date' : 'Archive date')}</span>
        <input type="date" value={sampleDate} aria-describedby={helpId}
          onChange={(event) => setSampleDate(event.target.value)} min="2000-01-01" max={archiveDate} />
      </label>
      <button type="submit" className="button subtle" disabled={submitting || pending || !canEdit}>
        <ArrowPathIcon aria-hidden="true" />{source === 'ml'
          ? t('Run ML preview') : sampleDate ? t('Fetch historical moisture') : t('Fetch latest moisture')}
      </button>
      <small id={helpId} className="muted">
        {t('Leave blank for the latest available data. ERA5-Land publishes with a delay of several days.')}
      </small>
    </form>
  )
}

function MoistureLegend({ layer, stale }: { layer: MoistureLayer; stale: boolean }) {
  const { number, t, date } = useLocale()
  const source = layer.source
  const value = layer.cells[0]?.value
  const model = source?.kind === 'ml' ? source : undefined
  return (
    <div className="map-legend regional-legend">
      <p><strong>{value === undefined ? t('Unavailable') : number(value, 3) + ' ' + layer.unit}</strong> · {model ? t('ML grid estimate') : t('Regional estimate')}</p>
      <small>{t('Data as of')} <time dateTime={layer.date}>{date(layer.date)}</time>
        {!model && <> · {t('Daily UTC mean')}</>}
        {source && <> · {t('Grid spacing')} {number(source.resolution_degrees)}° · <a href={source.url} target="_blank" rel="noreferrer">{source.name}</a></>}
      </small>
      {stale && <small className="moisture-detail moisture-stale">{t('Stale estimate · Data is over nine days old.')}</small>}
      {model?.experimental && <small className="moisture-detail moisture-stale">
        {t('Experimental. This model did not pass the release checks.')}
      </small>}
      {model?.interval && (
        <small className="moisture-detail">
          {model.interval.empirical_coverage == null ? number(model.interval.coverage * 100, 0) + '% ' + t('target interval') : t('Prediction interval')}{' '}
          {number(model.interval.lower, 3)}–{number(model.interval.upper, 3)} {layer.unit}
          {model.interval.empirical_coverage != null && <> · {t('Observed coverage')}{' '}
            {number(model.interval.empirical_coverage * 100, 0)}% {t('on held-out farms')}</>}
        </small>
      )}
      {model && (
        <small className="moisture-detail">{t('Model')} {model.model_version} · {t('Refreshed')}{' '}
          <time dateTime={model.generated_at}>{date(model.generated_at)}</time>
        </small>
      )}
      {model?.soil_profile && model.soil_source_url && (
        <small className="moisture-detail">
          <a href={model.soil_source_url} target="_blank" rel="noreferrer">USDA SSURGO</a>
          {' · '}{model.soil_profile.map_unit ?? model.soil_profile.component_name ?? t('Soil survey')}
          {model.soil_profile.clay !== null && ' · ' + number(model.soil_profile.clay, 1) + t('% clay')}
        </small>
      )}
    </div>
  )
}

function NewFieldForm({ season, points, undoPoint, onCreate, assisted, onSaving, onImport }: {
  season: number; points: Position[]; undoPoint?: () => void; onCreate: (field: Field) => void
  assisted?: boolean; onSaving?: (saving: boolean) => void; onImport?: (vertices: Position[]) => void
}) {
  const { t } = useLocale()
  const [manual, setManual] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string>()
  const nameInput = useRef<HTMLInputElement>(null)
  const areaInput = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const importGeneration = useRef(0)
  useEffect(() => () => { importGeneration.current++ }, [])

  async function importBoundary(file: File) {
    const generation = ++importGeneration.current
    setImporting(true)
    setError(undefined)
    try {
      const boundary = await readGeoJsonBoundary(file)
      if (generation !== importGeneration.current) return
      onImport?.(boundary.points)
      setManual(false)
      if (nameInput.current && !nameInput.current.value && boundary.name) nameInput.current.value = boundary.name
      if (areaInput.current) areaInput.current.value = estimateHectares(boundary.points).toFixed(2)
    } catch (failure) {
      if (generation === importGeneration.current) setError(errorText(failure))
    } finally {
      if (generation === importGeneration.current) setImporting(false)
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting || importing) return
    const values = new FormData(event.currentTarget)
    setError(undefined)
    setSubmitting(true)
    onSaving?.(true)
    try {
      const geometry = polygonFrom(manual ? parseVertices(String(values.get('vertices') ?? '')) : points)
      const field = await api.createField({
        name: String(values.get('name') ?? '').trim(),
        crop: cropInput(String(values.get('crop') ?? '').trim()),
        area_ha: assisted ? estimateHectares(points) : Number(values.get('area')),
        geometry,
        season,
      })
      onCreate(field)
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setSubmitting(false)
      onSaving?.(false)
    }
  }

  return (
    <section className="panel">
      <div className="section-heading"><div><h2>{t('Add field')}</h2>
        <p>{t(assisted ? 'Name the field and choose its crop to save the reviewed boundary.'
          : 'Click at least three vertices on the map or enter their coordinates.')}</p></div>
        {!assisted && <button className="button subtle" type="button" onClick={() => setManual(!manual)}>{manual ? t('Draw on map') : t('Enter coordinates')}</button>}</div>
      {!assisted && !manual && <div className="toolbar"><span role="status">{t('Vertices marked')} {points.length}</span>
        <button className="button subtle" type="button" disabled={!points.length} onClick={undoPoint}>{t('Undo vertex')}</button></div>}
      {error && <ErrorBox message={t(error)} />}
      <form className="form-grid" onSubmit={(event) => void submit(event)}>
        <label><span>{t('Field name')}</span><input required name="name" maxLength={80} ref={nameInput} /></label>
        <label><span>{t('Crop')}</span><input required name="crop" maxLength={60} /></label>
        {!assisted && <label><span>{t('Area (ha)')}</span><input required name="area" type="number" min=".01" step=".01" ref={areaInput} /></label>}
        {onImport && <label className="field-geojson-file"><span>{t('Import boundary GeoJSON')}</span>
          <input type="file" accept=".geojson,.json,application/geo+json" disabled={submitting || importing} onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) void importBoundary(file)
          }} />
        </label>}
        {manual && <label className="full-row"><span>{t('Vertices (longitude,latitude, one pair per line)')}</span>
          <textarea name="vertices" rows={4} required placeholder={'30.47,50.24\n30.48,50.24\n30.48,50.25'} /></label>}
        <div className="full-row"><button className="button primary" type="submit"
          disabled={submitting || importing || (Boolean(assisted) && points.length < 3)}>{submitting ? t('Saving…') : t('Save field')}</button></div>
      </form>
    </section>
  )
}
