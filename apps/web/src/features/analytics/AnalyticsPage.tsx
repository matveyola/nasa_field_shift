import { useCallback, useState } from 'react'
import { ArrowRightIcon, ArrowPathIcon, MapIcon, SparklesIcon } from '@heroicons/react/24/outline'
import { api } from '../../api/client'
import { planningBootstrap } from '../../api/planningWorkspace'
import type { AnalyticsPageProps, AnalyticsSection, MapFocus } from '../../shared/productContext'
import { analyticsSections } from '../../shared/productContext'
import { useResource } from '../../shared/useResource'
import { ErrorBox, Loading, ScenarioSelect } from '../../shared/ui'
import { FieldAnalysisPanel } from '../fields/FieldAnalysisPanel'
import { useFieldAnalytics } from '../fields/useFieldAnalytics'
import { SeasonalWeatherPanel } from '../fields/SeasonalWeatherPanel'
import { WaterPage } from '../water/WaterPage'
import { getCurrentWaterOutlook } from '../water/waterOutlook'
import { RotationsPage } from '../rotations/RotationsPage'
import { useAnalyticsCopy } from './copy'
import { overviewInsights, profileForField, vegetationEvidence } from './model'
import { ProfileSources, RotationHistory, SoilProfile } from './ProfileEvidence'
import './analytics.css'

const labels: Record<AnalyticsSection, string> = { overview: 'Overview', water: 'Water', vegetation: 'Vegetation', rotation: 'Rotations', soil: 'Soil' }

export function AnalyticsPage(props: AnalyticsPageProps) {
  // Scope every request, selection and job to the visible context. No old-season result flash.
  return <AnalyticsWorkspace key={[props.field.id, props.field.boundary_revision, props.context.season, props.context.scenario].join(':')} {...props} />
}

function AnalyticsWorkspace(props: AnalyticsPageProps) {
  const { field, fields, context, section } = props
  const { text, number, crop, date } = useAnalyticsCopy()
  const today = new Date().toISOString().slice(0, 10)
  const currentYear = Number(today.slice(0, 4))
  const seasons = Array.from(new Set([context.season, ...Array.from({ length: currentYear - 2017 + 4 }, (_, i) => currentYear + 3 - i)])).sort((a, b) => b - a)
  const state = useFieldAnalytics(field, context.season)
  const loadProfile = useCallback((signal: AbortSignal) => planningBootstrap(signal), [])
  const profiles = useResource(loadProfile)
  const profileState = profileForField(profiles.data?.profiles, field)
  const profile = profileState.status === 'available' ? profileState.profile : undefined
  const loadForecast = useCallback((signal: AbortSignal) => api.waterForecast(field.id, signal), [field.id, field.boundary_revision])
  const forecast = useResource(loadForecast, 5 * 60 * 1000)
  const outlook = getCurrentWaterOutlook(forecast.data, field.id, today, context.season)
  const satellite = vegetationEvidence(state.vegetation.data, field, context.season, today)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const loading = profiles.pending || forecast.pending || state.vegetation.pending
  const mapFocus = (extra: MapFocus = {}): MapFocus => ({ season: context.season, observationDate: state.primary?.date,
    comparisonDate: state.comparison?.date, comparison: false, index: 'ndvi', ...extra })
  const analysisProps = { state, field, season: context.season, onSeason: props.chooseSeason, embedded: true,
    heterogeneityVisible: false, terrainVisible: false,
    onSelectObservation: () => {},
    onHeterogeneity: () => props.openMapLayer('ndvi_variability', mapFocus()),
    onShowComparison: () => props.openMapLayer('ndvi_variability', mapFocus({ comparison: true })),
    onShowIndex: (index?: MapFocus['index']) => props.openMapLayer('ndvi_variability', mapFocus({ index: index ?? 'ndvi' })),
    onTerrain: () => props.openMapLayer('terrain', { season: context.season }),
  }
  const profileNotice = !profiles.pending && !profiles.error && !profile && <p className="analytics-empty" role="status">{text(profileState.status === 'boundary_changed'
    ? 'The saved profile uses an earlier boundary. Review and refresh it in Strategies.'
    : 'No saved profile for this field. Add its soil and crop history in Strategies.')}</p>

  return <div className="page-body analytics-page">
    <header className="analytics-heading"><div><span className="analytics-eyebrow">{text('Territory evidence')}</span><h1>{text('Analytics')}</h1>
      <p>{field.name} · {number(field.planting?.season === context.season ? field.planting.area_ha : field.area_ha)} {text('ha')} · {text('Recorded crop')}: {crop(field.crop)}</p></div>
      <button className="button" type="button" onClick={() => props.openAgent()}><SparklesIcon aria-hidden="true" />{text('Ask the assistant')}</button>
    </header>
    <div className="analytics-context" aria-label={text('Field context')}>
      <label className="control"><span>{text('Field')}</span><select value={context.field_id} onChange={e => props.chooseField(e.target.value)}>{fields.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      <label className="control"><span>{text('Season')}</span><select value={context.season} onChange={e => props.chooseSeason(Number(e.target.value))}>{seasons.map(year => <option key={year} value={year}>{year}</option>)}</select></label>
      <ScenarioSelect value={context.scenario} onChange={props.chooseScenario} />
    </div>
    <nav className="analytics-sections" aria-label={text('Analytics')}>{analyticsSections.map(id => <button key={id} type="button" aria-current={section === id ? 'page' : undefined}
      onClick={() => props.onSection(id)}>{text(labels[id])}</button>)}</nav>
    <div className="analytics-content" key={section}>
      {['overview', 'soil', 'rotation'].includes(section) && profile?.sources.some(source => source.kind === 'synthetic') && <p className="analytics-caution" role="status">{text('Synthetic profile — demonstration inputs, not field measurements.')}</p>}
      {section === 'overview' && <>
        <section className="analytics-intro"><span className="analytics-eyebrow">{text('Overview')} · {context.season}</span><h2>{text('What needs attention')}</h2>
          <p>{text('Start with the evidence, then compare your next crop.')}</p><button className="button primary" type="button" onClick={props.openStrategies}>{text('Review strategies')}<ArrowRightIcon aria-hidden="true" /></button></section>
        {loading ? <Loading text={text('Loading evidence…')} /> : <ol className="analytics-priorities">
          {overviewInsights(field, context.season, today, forecast.data, state.vegetation.data, profile).map((insight, index) => <li key={insight.section}>
            <span className="analytics-priority-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div><span className="analytics-eyebrow">{text(labels[insight.section])}</span><h3>{text(insight.code)}</h3></div>
            <button className="button subtle" type="button" onClick={() => props.onSection(insight.section)} aria-label={`${text('Open section')}: ${text(labels[insight.section])}`}><span>{text(labels[insight.section])}</span><ArrowRightIcon aria-hidden="true" /></button>
          </li>)}</ol>}
        <section className="analytics-evidence-index"><h2>{text('Evidence for this field')}</h2><dl>
          <div><dt>{text('Current forecast')}</dt><dd>{outlook.kind === 'unavailable' ? text('Not available') : `${date(outlook.firstDay.date)} – ${date(outlook.lastDay.date)}`}</dd></div>
          <div><dt>{text('Satellite observation')}</dt><dd>{satellite ? `${date(satellite.latest.date)} · ${number(satellite.latest.valid_percent, 0)}% ${text('Clear coverage').toLowerCase()}` : text('Not available')}</dd></div>
          <div><dt>{text('Saved profile')}</dt><dd>{profile ? `${text('Profile as of')} ${date(profile.as_of)}` : text('Not available')}</dd></div>
        </dl><button className="button subtle" type="button" onClick={() => { profiles.reload(); forecast.reload(); state.vegetation.reload() }}><ArrowPathIcon aria-hidden="true" />{text('Refresh')}</button></section>
        {forecast.error && <ErrorBox message={forecast.error} retry={forecast.reload} />}
        {state.vegetation.error && <ErrorBox message={state.vegetation.error} retry={state.vegetation.reload} />}
      </>}
      {section === 'water' && <>
        <p className="analytics-meta">{text('The live forecast concerns the coming days. Historical scenarios below use the selected season and assumptions.')}</p>
        <WaterPage {...props} embedded />
      </>}
      {section === 'vegetation' && <>
        <section className="analytics-vegetation-lead"><div><span className="analytics-eyebrow">Sentinel-2 · {context.season}</span><h2>{text(satellite ? 'Inspect patterns before diagnosing a crop' : 'No usable satellite observation in this season')}</h2>
          <p>{text(satellite ? 'NDVI describes vegetation reflectance. A low value alone does not identify drought, disease or lost yield.' : 'Refresh satellite processing or inspect the field. Missing imagery is not evidence of healthy vegetation.')}</p>
          {satellite && <p className="analytics-meta"><time dateTime={satellite.latest.date}>{date(satellite.latest.date)}</time> · {number(satellite.latest.valid_percent, 0)}% {text('Clear coverage')}</p>}
          {satellite?.historical && <p className="analytics-caution">{text('Historical season — this is not current crop condition.')}</p>}
          {satellite && !satellite.historical && satellite.ageDays > 30 && <p className="analytics-caution">{text('Older than 30 days — inspect before acting on this observation.')}</p>}
          <button className="button" type="button" onClick={() => props.openMapLayer('ndvi_variability', mapFocus())}><MapIcon aria-hidden="true" />{text('Open vegetation map')}</button>
        </div>{satellite && <div className="analytics-ndvi-figure"><strong>{number(satellite.latest.mean_ndvi, 2)}</strong><span>NDVI · {date(satellite.latest.date)}</span></div>}</section>
        <FieldAnalysisPanel {...analysisProps} sections={['vegetation', 'precision']} />
        <details className="analytics-disclosure"><summary>{text('Seasonal weather evidence')}</summary><div className="analytics-details-body"><SeasonalWeatherPanel field={field} season={context.season} /></div></details>
      </>}
      {section === 'rotation' && <>
        {profiles.pending && <Loading text={text('Loading evidence…')} />}{profileNotice}
        <RotationHistory profile={profile} season={context.season} />
        <button className="button primary" type="button" onClick={props.openStrategies}>{text('Review strategies')}<ArrowRightIcon aria-hidden="true" /></button>
        <details className="analytics-disclosure" onToggle={event => setDiagnosticsOpen(event.currentTarget.open)}><summary>{text('Saved sequence diagnostics')}</summary>
          <div className="analytics-details-body"><p>{text('Compare saved sequences against historical weather. Edit future choices in Strategies.')}</p>
            {diagnosticsOpen && <RotationsPage {...props} embedded openWater={() => props.onSection('water')} />}
          </div>
        </details>
        {profile && <ProfileSources profile={profile} />}
      </>}
      {section === 'soil' && <>
        {profiles.pending && <Loading text={text('Loading evidence…')} />}{profileNotice}
        <SoilProfile profile={profile} />
        <button className="button primary" type="button" onClick={props.openStrategies}>{text('Review strategies')}<ArrowRightIcon aria-hidden="true" /></button>
        <section className="analytics-terrain-lead"><div><h2>{text('Terrain is a cue for inspection, not a flood forecast')}</h2><p>{text('Surface gradients do not measure drainage pipes, runoff or waterlogging probability.')}</p></div>
          <button className="button" type="button" onClick={() => props.openMapLayer('terrain', { season: context.season })}><MapIcon aria-hidden="true" />{text('Open map')}</button></section>
        <details className="analytics-disclosure"><summary>{text('Terrain evidence and processing')}</summary><div className="analytics-details-body"><FieldAnalysisPanel {...analysisProps} sections={['terrain']} /></div></details>
        {profile && <ProfileSources profile={profile} />}
      </>}
      {['overview', 'soil', 'rotation'].includes(section) && profiles.error && <ErrorBox message={profiles.error} retry={profiles.reload} />}

    </div>
  </div>
}
