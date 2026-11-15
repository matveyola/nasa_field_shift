import { useLocale } from '../../shared/locale'
import type { useFieldAnalytics } from './useFieldAnalytics'
import { spectralDefinitions } from './spectralIndices'
import type { SpectralIndex } from '../../api/analytics'

type Props = {
  state: ReturnType<typeof useFieldAnalytics>
  reveal: () => void
  openAnalytics?: () => void
}
/** Map controls keep their exact observation; charts, exports and diagnostics live in Analytics. */
export function MapObservationControls({ state, reveal, openAnalytics }: Props) {
  const { t, shortDate } = useLocale()
  return <section className="map-observation-controls">
    <h3>{t('Map observations')}</h3>
    {state.vegetation.pending && <p role="status">{t('Loading selected scene…')}</p>}
    {!state.primary && state.observations.length > 0 && <p role="status">{t('Selected observation is unavailable. Choose an available date.')}</p>}
    <label className="control"><span>{t('Observation date')}</span>
      <select value={state.primary?.date ?? ''} disabled={!state.observations.length} onChange={event => { state.selectObservation(event.target.value); reveal() }}>
        {!state.primary && <option value="">{t('Unavailable')}</option>}
        {state.observations.map(point => <option key={point.scene_id + point.date} value={point.date}>{shortDate(point.date)}</option>)}
      </select>
    </label>
    <label className="control"><span>{t('Comparison date')}</span>
      <select value={state.comparison?.date ?? ''} onChange={event => { state.selectComparison(event.target.value || undefined); reveal() }}>
        <option value="">{t('No comparison')}</option>
        {state.alternatives.map(point => <option key={point.scene_id + point.date} value={point.date}>{shortDate(point.date)}</option>)}
      </select>
    </label>
    <label className="control"><span>{t('Spectral index')}</span>
      <select value={state.spectralIndex ?? 'ndvi'} onChange={event => { state.showIndex(event.target.value as SpectralIndex); reveal() }}>
        {Object.keys(spectralDefinitions).map(index => <option key={index} value={index}>{index.toUpperCase()}</option>)}
      </select>
    </label>
    {openAnalytics && <button type="button" className="button subtle" onClick={openAnalytics}>{t('Open full analytics')}</button>}
  </section>
}
