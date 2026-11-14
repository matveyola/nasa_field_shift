import { ArrowDownTrayIcon, ArrowPathIcon, MapIcon } from '@heroicons/react/24/outline'
import type { Field } from '../../api/contracts'
import { ContextHelp } from '../../shared/ContextHelp'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import type { useFieldAnalytics } from './useFieldAnalytics'
import { SeasonalNdviChart } from './SeasonalNdviChart'
import { SeasonalWeatherPanel } from './SeasonalWeatherPanel'
import { NdviHistoryPanel } from './precision/NdviHistoryPanel'
import { PrecisionFieldPanel } from './precision/PrecisionFieldPanel'
import { vegetationCsv } from './vegetationInsights'
import { SpectralIndexPanel } from './SpectralIndexPanel'
import { fieldHelp, sceneHelpMetadata, spectralHelp } from './fieldHelp'
import { useAccess } from '../access/AccessProvider'
import './fieldAnalytics.css'

type Props = {
  state: ReturnType<typeof useFieldAnalytics>
  field: Field
  season: number
  onSeason: (year: number) => void
  heterogeneityVisible: boolean
  terrainVisible: boolean
  onHeterogeneity: () => void
  onSelectObservation: () => void
  onShowComparison: () => void
  onShowIndex: () => void
  onTerrain: () => void
}

export function FieldAnalysisPanel({ state, field, season, onSeason, heterogeneityVisible, terrainVisible, onHeterogeneity, onSelectObservation, onShowComparison, onShowIndex, onTerrain }: Props) {
  const { t, number, date, language } = useLocale()
  const { canEdit } = useAccess()
  const { vegetation, terrain, observations, primary: selected, comparison, alternatives, selectedScene } = state
  const result = vegetation.data
  const pending = result?.months.some((month) => month.status === 'pending')
  const layer = selectedScene.data?.heterogeneity
  const todayYear = new Date().getUTCFullYear()
  const help = fieldHelp[language]

  function downloadObservations() {
    const url = URL.createObjectURL(new Blob([vegetationCsv(observations)], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `ndvi-${field.id.replace(/[^a-z0-9_-]/gi, '_')}-${season}.csv`
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div className="field-analysis-panel">
    <section aria-label={t('Seasonal NDVI')}>
      <header className="field-analysis-heading">
        <div className="field-help-row"><h3>{t('Seasonal NDVI')}</h3>
          <ContextHelp label={t('Seasonal NDVI')}><p>{spectralHelp.ndvi[language]}</p></ContextHelp>
        </div>
        <label className="ndvi-season"><span className="sr-only">{t('Season')}</span><select value={season} onChange={(event) => onSeason(Number(event.target.value))}>
          {Array.from({ length: todayYear - 2016 }, (_, index) => todayYear - index).map((year) => <option key={year}>{year}</option>)}
        </select></label>
      </header>
      <p className="analysis-description">{t('How vegetation changes across the season.')}</p>
      {result?.boundary_history_assumed && <p className="analysis-footnote">{language === 'uk'
        ? 'Для цього сезону немає підтвердженої історичної межі. Використано найраніший доступний контур.'
        : 'This season has no recorded historical boundary. The earliest available contour is reused.'}</p>}
      {vegetation.error && <ErrorBox message={vegetation.error} retry={vegetation.reload} />}
      {selected ? <>
        <div className="ndvi-current"><strong>{number(selected.mean_ndvi, 2)}<small>NDVI</small></strong>
          <div className="ndvi-observation-meta">
            <div><time dateTime={selected.date}>{date(selected.date)}</time><span>{number(selected.valid_percent, 0)}% {t('clear coverage')}</span></div>
            <ContextHelp label={help.scene.title}>
              <p>{help.scene.description}</p>
              <dl className="scene-help-metadata">
                {sceneHelpMetadata(language, {
                  source: selected.catalog_url ? 'Sentinel-2 L2A' : undefined,
                  date: selected.date,
                  coveragePercent: selected.valid_percent,
                  analysisResolutionMetres: result?.resolution_m,
                }).map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
              </dl>
            </ContextHelp>
          </div>
        </div>
        <SeasonalNdviChart observations={observations} season={season} selected={selected.date}
          onSelect={(day) => { state.selectObservation(day); onSelectObservation() }} />
        <a className="analysis-source" href={selected.catalog_url} target="_blank" rel="noreferrer">Sentinel-2 L2A · {result?.resolution_m} {t('m')} · {t('View source scene')}</a>
        {selected.mean_msavi != null && <p className="analysis-description">{t('Mean MSAVI')} · {number(selected.mean_msavi, 2)}</p>}
        {(selected.mean_msavi != null || state.showingMsavi) && <button type="button" className="button subtle analysis-map-toggle"
          onClick={() => { state.showMsavi(!state.showingMsavi); onShowIndex() }}>
          <MapIcon aria-hidden="true" />{t(state.showingMsavi ? 'Show NDVI on map' : 'Show MSAVI on map')}
        </button>}
        <button type="button" className="button subtle analysis-refresh" onClick={downloadObservations}>
          <ArrowDownTrayIcon aria-hidden="true" />{t('Download NDVI observations (CSV)')}
        </button>
        {alternatives.length > 0 && <div className="ndvi-comparison">
          <label><span>{t('Compare with another observation')}</span>
            <select value={comparison?.date ?? ''} onChange={(event) => state.selectComparison(event.target.value || undefined)}>
              <option value="">{t('Choose a date')}</option>
              {alternatives.map((point) => <option key={point.date} value={point.date}>{date(point.date)}</option>)}
            </select>
          </label>
          {comparison && <>
            <dl className="ndvi-comparison-values">
              {[selected, comparison].map((point, index) => <div key={point.scene_id}>
                <dt>{index === 0 ? 'A' : 'B'} · {date(point.date)}</dt>
                <dd>{number(point.mean_ndvi, 2)} NDVI · {number(point.valid_percent, 0)}%</dd>
              </div>)}
            </dl>
            <p className="analysis-footnote">{t('Coverage can differ between scenes. Both maps use the same NDVI scale.')}</p>
            <button type="button" className="button subtle analysis-map-toggle" onClick={onShowComparison}>
              <MapIcon aria-hidden="true" />{t('Compare dates on the map')}
            </button>
          </>}
        </div>}
      </> : <p className="analysis-empty" role="status">{t(vegetation.pending ? 'Loading satellite observations…' : pending ? 'Processing seasonal imagery…' : 'No clear observations saved for this season.')}</p>}
      <p className="analysis-footnote">{t('One representative clear scene per month. Gaps are not interpolated. The band shows variation within the field, not forecast uncertainty.')}</p>
      <button type="button" className="button subtle analysis-refresh" disabled={Boolean(state.submitting) || pending || !canEdit}
        onClick={() => void state.refresh('vegetation')}><ArrowPathIcon aria-hidden="true" />
        {t(pending || state.submitting === 'vegetation' ? 'Processing seasonal imagery…' : 'Update satellite analysis')}</button>
      {result && result.months.length > 0 && <details className="analysis-quality"><summary>{t('Coverage and processing')} · {observations.length}/{result.months.length}</summary>
        <ul>{result.months.map((month) => <li key={month.month}><span>{date(`${season}-${String(month.month).padStart(2, '0')}-01`)}</span><span title={month.reason ? t(month.reason) : undefined}>{t({ ready: 'Ready', no_clear_scene: 'No clear scene', pending: 'Processing', missing: 'Not processed', failed: 'Processing failed', unsupported: 'Outside supported coverage' }[month.status])}</span></li>)}</ul>
        {result.limitations.map((limitation) => <p key={limitation}>{t(limitation)}</p>)}
      </details>}
    </section>
    <SpectralIndexPanel observations={observations} selected={selected}
      onSelect={(day) => { state.selectObservation(day); onSelectObservation() }}
      onMap={(index) => { state.showIndex(index); onShowIndex() }} />
    <section className="field-heterogeneity" aria-label={t('Within-field variation')}>
      <header className="field-analysis-heading">
        <div className="field-help-row"><h3>{t('Within-field variation')}</h3>
          <ContextHelp label={help.variation.title}><p>{help.variation.description}</p></ContextHelp>
        </div>
      </header>
      {selectedScene.error && <ErrorBox message={selectedScene.error} retry={selectedScene.reload} />}
      {layer ? <>
        <p className="analysis-description">{t('Selected observation')} · <time dateTime={layer.date}>{date(layer.date)}</time> · {layer.resolution_m} {t('m')}</p>
        <div className="heterogeneity-value"><strong>{number(layer.low_area_percent, 1)}<small>%</small></strong><span>{t('of observed area has lower NDVI')}</span></div>
        <div className="heterogeneity-legend"><span><i className="zone-low" />{t('Lower')}</span><span><i className="zone-typical" />{t('Typical')}</span><span><i className="zone-high" />{t('Higher')}</span></div>
        <p className="analysis-footnote">{t('Relative to this field’s median by ±0.15 NDVI. A cue for inspection, not a diagnosis. Unobserved areas remain transparent.')}</p>
        <button type="button" className="button subtle analysis-map-toggle" aria-pressed={heterogeneityVisible} onClick={onHeterogeneity}>
          <MapIcon aria-hidden="true" />{t(heterogeneityVisible ? 'Hide variation layer' : 'Show variation on map')}</button>
      </> : <p className="analysis-empty" role="status">{t(selectedScene.pending ? 'Loading selected scene…' : selected ? 'No saved map for this observation.' : 'The variation map appears after a usable satellite observation.')}</p>}
    </section>
    <SeasonalWeatherPanel field={field} season={season} />
    <NdviHistoryPanel field={field} season={season} layer={layer} fingerprint={selectedScene.data?.geometry_fingerprint ?? ''} />
    <PrecisionFieldPanel field={field} season={season} layer={layer} sceneId={selectedScene.data?.observation.scene_id}
      fingerprint={selectedScene.data?.geometry_fingerprint} />
    <section className="field-terrain-analysis" aria-label={t('Terrain directions')}>
      <header className="field-analysis-heading">
        <div className="field-help-row"><h3>{t('Terrain directions')}</h3>
          <ContextHelp label={help.terrain.title}><p>{help.terrain.description}</p></ContextHelp>
        </div>
      </header>
      <p className="analysis-description">{t('Potential downhill directions from the land surface.')}</p>
      {terrain.error && <ErrorBox message={terrain.error} retry={terrain.reload} />}
      {terrain.data?.status === 'ready' ? <>
        <dl className="terrain-readout"><div><dt>{t('Elevation range')}</dt><dd>{number(terrain.data.elevation_min_m!)}–{number(terrain.data.elevation_max_m!)} {t('m')}</dd></div>
          <div><dt>{t('Coverage')}</dt><dd>{number(terrain.data.coverage_percent, 0)}%</dd></div></dl>
        {!terrain.data.vectors.length && <p className="analysis-empty">{t('No reliable downhill direction at this resolution.')}</p>}
        <button type="button" className="button subtle analysis-map-toggle" disabled={!terrain.data.vectors.length} aria-pressed={terrainVisible} onClick={onTerrain}>
          <MapIcon aria-hidden="true" />{t(terrainVisible ? 'Hide terrain directions' : 'Show terrain directions')}</button>
        <a className="analysis-source" href={terrain.data.source_url} target="_blank" rel="noreferrer">{terrain.data.source} · {terrain.data.source_resolution_m} {t('m')}</a>
        <details className="analysis-quality"><summary>{t('Source and limitations')}</summary>
          <p>{terrain.data.attribution}</p>{terrain.data.limitations.map((limitation) => <p key={limitation}>{t(limitation)}</p>)}
        </details>
      </> : <>
        {terrain.data?.reason && <p className="analysis-footnote">{t(terrain.data.reason)}</p>}
        <button type="button" className="button subtle analysis-refresh" disabled={Boolean(state.submitting) || state.terrainPending || !canEdit}
          onClick={() => void state.refresh('terrain')}><ArrowPathIcon aria-hidden="true" />{t(state.terrainPending || state.submitting === 'terrain' ? 'Processing terrain…' : 'Calculate terrain directions')}</button>
      </>}
      <p className="analysis-footnote">{t('30 m surface model. This does not measure runoff, drainage pipes or flooding; small furrows and ditches may be unresolved.')}</p>
    </section>
    {state.error && <ErrorBox message={state.error} />}
  </div>
}
