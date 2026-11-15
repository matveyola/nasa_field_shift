import { cropInput, useLocale } from '../../shared/locale'
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { AdjustmentsHorizontalIcon, ArrowPathIcon, ChartBarIcon, ChevronRightIcon, MapIcon, PencilSquareIcon, PlusIcon, ViewfinderCircleIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { api, ApiError } from '../../api/client'
import type { Field, MoistureLayer, MoistureSource, Position } from '../../api/contracts'
import { JobStatus } from '../../shared/JobStatus'
import { ErrorBox, PageHeading } from '../../shared/ui'
import { errorText } from '../../shared/useResource'
import { FieldContextPanel } from './FieldContextPanel'
import { FieldPanelResizer } from './FieldPanelResizer'
import type { BasemapId } from './basemaps'
import { FieldGeometryEditor } from './FieldGeometryEditor'
import { fieldGeometryDraft, recordShape, undoShape, type FieldEditMode, type FieldGeometryDraft } from './fieldGeometryDraft'
import { estimateHectares, parseVertices, polygonFrom } from './geometry'
import { readGeoJsonBoundary } from './geoJsonBoundary'
import { MapLayerPicker, type MapLayer } from './MapLayerPicker'
import { MoistureForecastPanel, useMoistureForecast } from './MoistureForecastPanel'
import { useSatelliteLayer } from './satellite'
import { useBoundarySuggestion } from './useBoundarySuggestion'
import { useMoistureOverlay, type MoistureOverlay } from './useMoistureOverlay'
import { useFieldAnalytics } from './useFieldAnalytics'
import { ndviBands } from './vegetationInsights'
import { spectralCell, spectralColor, spectralDefinitions } from './spectralIndices'
import { BoundaryHistoryPanel } from './BoundaryHistoryPanel'
import { PreparedContoursPanel, type MapWindow } from './PreparedContoursPanel'
import type { PreparedContour } from '../../api/boundaries'
import { useAccess } from '../access/AccessProvider'
import './map.css'
import './fieldWorkspace.css'

const FieldMap = lazy(() => import('./FieldMap'))
const FieldAnalysisPanel = lazy(() => import('./FieldAnalysisPanel').then((module) => ({ default: module.FieldAnalysisPanel })))
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
  chooseField: (id: string) => void
  onCreate: (field: Field) => void
  onUpdate: (field: Field) => void
  reloadFields: () => void
  openForecast: (fieldId: string, day?: string) => void
  onSetup?: () => void
  setupLabel?: string
}

export function FieldsPage({ fields, selected, season, initialDrawing = false, initialAnalysis, showNdviLayer, chooseField, onCreate, onUpdate, reloadFields, openForecast, onSetup, setupLabel }: Props) {
  const { t, number, date, shortDate } = useLocale()
  const { canEdit } = useAccess()
  const [preparedContours, setPreparedContours] = useState<PreparedContour[]>([])
  const [contourFocus, setContourFocus] = useState<Position>()
  const [viewport, setViewport] = useState<MapWindow>()
  const [activeLayers, setActiveLayers] = useState<ReadonlySet<MapLayer>>(() => {
    const layers = new Set<MapLayer>(['forecast', 'historical', 'boundaries', 'planting'])
    if (showNdviLayer) layers.add('heterogeneity')
    return layers
  })
  useEffect(() => {
    if (showNdviLayer) setActiveLayers((layers) => layers.has('heterogeneity') ? layers : new Set([...layers, 'heterogeneity']))
  }, [showNdviLayer])
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
  const [showSatellite, setShowSatellite] = useState(false)
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
  const [analysisSeason, setAnalysisSeason] = useState(Math.min(season, new Date().getUTCFullYear()))
  const analytics = useFieldAnalytics(assistantField, analysisSeason)
  const sceneLayer = analytics.activeScene.data?.heterogeneity
  const comparingNdvi = Boolean(analytics.comparison && !analytics.spectralIndex)
  const vegetationIndex = analytics.spectralIndex ?? (comparingNdvi ? 'ndvi' : 'relative')
  const sceneHasLayer = sceneLayer && (vegetationIndex === 'relative' || sceneLayer.cells.some((cell) => spectralCell(cell, vegetationIndex) != null))
  const historicalGeometry = inspecting && activeLayers.has('heterogeneity') && analysisSeason !== season
    ? analytics.vegetation.data?.boundary_geometry : undefined
  const mapFields = useMemo(() => historicalGeometry
    ? fields.map((field) => field.id === selectedId ? { ...field, geometry: historicalGeometry, planting: null } : field)
    : fields, [fields, selectedId, historicalGeometry])
  const mapSelected = mapFields.find((field) => field.id === selectedId)
  const forecastState = useMoistureForecast(inspecting ? selectedId : undefined, boundaryRevision)
  const showForecast = inspecting && Boolean(selectedId) && activeLayers.has('forecast')
  const currentSurface = forecastState.days.find((day) => day.date === forecastState.today)
  const satelliteActive = showSatellite && !drawingMap && selectedId !== undefined
  const satellite = useSatelliteLayer(selectedId, satelliteActive, satelliteDate, boundaryRevision)
  const scene = satellite.data?.scene
  const overlayField = (source: MoistureSource) => inspecting && activeLayers.has(source) ? selectedId : undefined
  const historical = useMoistureOverlay('historical', overlayField('historical'), season, boundaryRevision)
  const model = useMoistureOverlay('ml', overlayField('ml'), season, boundaryRevision)
  const demo = useMoistureOverlay('demo', overlayField('demo'), season, boundaryRevision)
  const overlays = [historical, model, demo].filter(({ source }) => overlayField(source) !== undefined)
  const configurableOverlays = overlays.filter(({ canRefresh }) => canRefresh)
  const moistureLayers = useMemo(() => [historical.layer, model.layer, demo.layer]
    .filter((layer): layer is MoistureLayer => layer !== undefined), [historical.layer, model.layer, demo.layer])
  const hasSettings = inspecting && (satelliteActive || configurableOverlays.length > 0)
  const basemap = preferredBasemap ?? 'aerial'
  const mapLayers: Array<{ id: MapLayer; label: string }> = [historical, model, demo]
    .map(({ source: id, layer }) => {
      const layerDepth = layer ? [...new Set(layer.depth_cm)].join('–') + ' ' + t('cm') : undefined
      const label = layer?.source?.kind === 'ml'
        ? layer.source.experimental ? 'Experimental ML' : 'ML surface moisture' : moistureSources[id]
      return { id, label: t(label) + (layerDepth ? ' · ' + layerDepth : '') }
    })
  mapLayers.unshift({ id: 'forecast', label: t('Today’s soil moisture · IFS') })
  mapLayers.push({ id: 'boundaries', label: t('Field boundaries') })
  mapLayers.push({ id: 'heterogeneity', label: t('Within-field variation · NDVI') })
  mapLayers.push({ id: 'terrain', label: t('Terrain directions · 30 m') })
  if (selected?.planting) mapLayers.push({ id: 'planting', label: t('Planted area') })

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
      if (edit.mode === 'planting') setActiveLayers((current) => new Set([...current, 'planting']))
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

  function toggleLayer(id: MapLayer) {
    setActiveLayers((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function revealNdviLayer() {
    setActiveLayers((current) => current.has('heterogeneity') ? current : new Set([...current, 'heterogeneity']))
  }

  return (
    <div className="page-body fields-page">
      <PageHeading title="Fields" season={season} />
      <div className="fields-content">
        {onSetup && <div className="field-setup-banner">
          <button type="button" className="button primary" onClick={onSetup}>
            <PlusIcon aria-hidden="true" />{setupLabel ?? t('Add field')}
          </button>
        </div>}
        <div className={'fields-layout' + (assistantField ? '' : ' is-editing')}>
          {assistantField && <FieldContextPanel field={assistantField}
            initialAnalysis={initialAnalysis} mapFirst={showNdviLayer} vegetation={analytics.vegetation.data}
            analysis={(inspectMap) => <Suspense fallback={<p role="status">{t('Loading field analysis…')}</p>}>
              <FieldAnalysisPanel state={analytics} field={assistantField} season={analysisSeason} onSeason={setAnalysisSeason}
              heterogeneityVisible={activeLayers.has('heterogeneity')} terrainVisible={activeLayers.has('terrain')}
              onSelectObservation={revealNdviLayer} onShowComparison={() => { analytics.showComparison(true); revealNdviLayer(); inspectMap() }}
              onShowIndex={() => { revealNdviLayer(); inspectMap() }}
              onHeterogeneity={() => { toggleLayer('heterogeneity'); inspectMap() }} onTerrain={() => { toggleLayer('terrain'); inspectMap() }} />
            </Suspense>}
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
                {showForecast && <CurrentMoisture state={forecastState} />}
                {satelliteActive && <div className="map-legend regional-legend satellite-scene" role="status">
                  {scene ? <>
                    <p><strong>Sentinel-2 · <time dateTime={scene.acquired_at}>{date(scene.acquired_at)}</time></strong></p>
                    <small>{t('10 m true color ·')} {number(scene.tile_cloud_percent, 1)}{t('% tile cloud cover')}</small>
                    <small className="moisture-detail">{t('Contains modified Copernicus Sentinel data')} {scene.acquired_at.slice(0, 4)} ·{' '}
                      <a href={scene.catalog_url} target="_blank" rel="noreferrer">{t('Earth Search scene')}</a></small>
                  </> : satellite.error ? <>
                    <p>{t(satellite.error)}</p>
                    <button className="button subtle" type="button" onClick={satellite.reload}>{t('Retry satellite')}</button>
                  </> : satellite.pending || !satellite.data ? <p>{t('Loading Sentinel-2 image…')}</p>
                    : <p>{t('No suitable Sentinel-2 scene in the preceding 35 days.')}</p>}
                </div>}
                {overlays.map((overlay) => <MoistureOverlayLegend key={overlay.source} overlay={overlay} />)}
                {tileFailure && <p className="map-fallback" role="status">{t('Basemap tiles unavailable. Field boundaries remain visible.')}
                  <button type="button" className="button subtle" onClick={() => { setTileFailure(false); setRetryTiles((value) => value + 1) }}>{t('Retry')}</button>
                </p>}
                {localAerial && <p className="map-fallback" role="status">{t('Live tiles unavailable. Showing a saved USGS aerial image of this area.')}</p>}
                {!overlays.length && !satelliteActive && !showForecast && !drawing && (
                  <span className="map-layer-label">{activeLayers.has('boundaries') ? t('Field boundaries') : t('No overlays enabled')}</span>
                )}
              </div>
              {hasSettings && <div id="field-map-settings" className="map-settings-panel">
                {satelliteActive && <label className="control satellite-date">
                  <span>{t('Satellite on or before')}</span>
                  <input type="date" value={satelliteDate} min="2015-06-23" max={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => setSatelliteDate(event.target.value)} />
                </label>}
                {configurableOverlays.map((overlay) => <MoistureControls key={overlay.source} overlay={overlay} />)}
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
                <FieldMap fields={mapFields} selected={mapSelected} layers={moistureLayers} showBoundaries={activeLayers.has('boundaries')}
                  vegetation={inspecting && activeLayers.has('heterogeneity') && sceneHasLayer ? sceneLayer : undefined}
                  vegetationIndex={vegetationIndex}
                  terrain={inspecting && activeLayers.has('terrain') ? analytics.terrain.data : undefined}
                  bottomOcclusion={sheetOcclusion.fieldId === selectedId ? sheetOcclusion.height : 0}
                  showPlanting={!search && activeLayers.has('planting')}
                  satellite={edit?.suggestion?.preview ?? search?.preview ?? (satelliteActive ? satellite.data?.raster : undefined)}
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
                  layers={mapLayers} activeLayers={activeLayers} satelliteActive={satelliteActive}
                  satelliteAvailable={Boolean(selectedId) && !drawingMap} onBasemap={selectBasemap}
                  onLayer={toggleLayer} onSatellite={() => setShowSatellite((shown) => !shown)} />
              </div>
              {inspecting && (activeLayers.has('heterogeneity') || activeLayers.has('terrain')) && <div className="map-analytics-legend">
                {activeLayers.has('heterogeneity') && <>
                  <strong>{analytics.spectralIndex ? analytics.spectralIndex.toUpperCase() : comparingNdvi ? t('NDVI comparison') : t('Within-field variation')}</strong>
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
                        ? <div className="heterogeneity-legend ndvi-scale">{ndviBands.map(({ min, color }, index) => {
                          const range = spectralDefinitions[vegetationIndex].range
                          const low = vegetationIndex === 'ndvi' ? min : range[0] + (range[1] - range[0]) * index / 5
                          const high = vegetationIndex === 'ndvi' ? ndviBands[index + 1]?.min ?? 1 : range[0] + (range[1] - range[0]) * (index + 1) / 5
                          return <span key={index}><i style={{ backgroundColor: vegetationIndex === 'ndvi' ? color : spectralColor((low + high) / 2, vegetationIndex) }} />{index === 0 ? '≤ ' : ''}{number(low, 2)}–{number(high, 2)}{index === 4 ? ' ≥' : ''}</span>
                        })}</div>
                        : <div className="heterogeneity-legend"><span><i className="zone-low" />{t('Lower')}</span><span><i className="zone-typical" />{t('Typical')}</span><span><i className="zone-high" />{t('Higher')}</span></div>}
                      <small>{date(sceneLayer.date)} · Sentinel-2 · {sceneLayer.resolution_m} {t('m')}</small>
                    </> : <small>{t(analytics.activeScene.error ? 'Could not load this scene.' : analytics.activeScene.pending ? 'Loading selected scene…'
                      : analytics.showingMsavi ? 'MSAVI is unavailable for this observation.'
                        : analytics.primary ? 'No saved map for this observation.' : 'Update satellite analysis to load this layer.')}</small>}
                  </div>
                  {analytics.activeScene.error && <button className="ndvi-retry" type="button" onClick={analytics.activeScene.reload}>{t('Retry')}</button>}
                </>}
                {activeLayers.has('terrain') && <><strong>{t('Terrain directions')}</strong><small>{t(analytics.terrain.data?.status === 'ready'
                  ? analytics.terrain.data.vectors.length ? 'Potential downhill directions · 30 m' : 'No reliable downhill direction at this resolution.'
                  : 'Calculate terrain directions to load this layer.')}</small></>}
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
        {overlays.map(({ source, resource, submitError, jobId, job, jobError }) => (resource.error || submitError || jobId) && (
          <div className="map-job-status" key={source} aria-label={t(moistureSources[source])}>
            {resource.error && <ErrorBox message={resource.error} retry={resource.reload} />}
            {submitError && <ErrorBox message={submitError} />}
            {jobId && <JobStatus job={job} error={jobError} />}
          </div>
        ))}
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
    <div className="moisture-layer-detail current-moisture">
      <h3>{t('Today’s soil moisture · IFS')}</h3>
      {forecast && current ? (
        <div className="map-legend regional-legend">
          <p><strong>{number(current.ifs_value, 3)} {forecast.unit}</strong>{' · '}
            <time dateTime={current.date}>{date(current.date)}</time></p>
          <small>{t('Daily UTC estimate · 0–7 cm')} ·{' '}
            <a href={forecast.source_url} target="_blank" rel="noreferrer">{forecast.provider}</a> · {number(forecast.resolution_km)} {t('km')}</small>
        </div>
      ) : <p className="map-layer-label" role="status">{saved.pending ? t('Loading forecast…') : t('No forecast available for today.')}</p>}
    </div>
  )
}

function MoistureOverlayLegend({ overlay }: { overlay: MoistureOverlay }) {
  const { t } = useLocale()
  const { source, layer, resource, stale, pending, sampleDate } = overlay
  return (
    <div className="moisture-layer-detail">
      <h3><span className={'map-layer-swatch map-swatch-' + source} aria-hidden="true" />{t(moistureSources[source])}</h3>
      {layer ? <MoistureLegend layer={layer} stale={stale} /> : (
        <p className="map-layer-label" role="status">
          {resource.pending || pending ? t('Loading moisture…')
            : resource.error ? t('Unavailable')
              : sampleDate ? t('No saved estimate for this date.') : t('No saved moisture estimate yet.')}
        </p>
      )}
    </div>
  )
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
  const { number, shortDate, t, date } = useLocale()
  if (layer.demo) return (
    <div className="map-legend">
      <div><span>{number(layer.minimum, 2)}</span><i /><span>{number(layer.maximum, 2)} {layer.unit}</span></div>
      <small>{shortDate(layer.date)} · {t('Simulated preview')}</small>
    </div>
  )
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
