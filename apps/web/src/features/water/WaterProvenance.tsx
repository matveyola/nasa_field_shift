import { useLocale } from '../../shared/locale'
import type { WaterProvenance as Provenance } from '../../api/contracts'

export function WaterProvenance({ data }: { data: Provenance }) {
  const { t, number, date } = useLocale()
  return (
    <details className="panel analysis-details">
      <summary>{t('Data sources and model inputs')}</summary>
      <dl className="data-strip analysis-source-grid">
        <div><dt>{t('Weather')}</dt><dd>{data.weather_model} · {t('Grid spacing')} {number(data.grid_resolution_degrees)}°</dd></div>
        <div><dt>{t('Dates')}</dt><dd>{date(data.start_date)} – {date(data.end_date)}</dd></div>
        <div><dt>{t('Initial reserve')}</dt><dd>{number(data.initial_reserve_mm)} {t('mm')}</dd></div>
        <div><dt>{t('Soil capacity')}</dt><dd>{number(data.capacity_mm)} {t('mm')}</dd></div>
        <div><dt>{t('Rainfall factor')}</dt><dd>{number(data.rainfall_multiplier)}×</dd></div>
        <div><dt>{t('Model')}</dt><dd>{data.model_version}</dd></div>
      </dl>
      <p className="muted">{t('Initial reserve from')} {data.initial_reserve_source === 'era5_prior_day'
        ? t('previous-day ERA5 soil moisture') : t('a user-provided value')}. {t('Dates use')} {data.timezone}.</p>
      <ul className="analysis-limitations">
        {data.limitations.map((limitation) => <li key={limitation}>{t(limitation)}</li>)}
      </ul>
      <div className="analysis-source-links">
        <a href={data.weather_url} target="_blank" rel="noreferrer">{data.weather_source}</a>
        <a href="https://www.fao.org/4/X0490E/x0490e0e.htm" target="_blank" rel="noreferrer">{t('FAO-56 water balance')}</a>
      </div>
    </details>
  )
}
