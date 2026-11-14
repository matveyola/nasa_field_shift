import type { FieldProfileSnapshot, Reason, WeatherScenarioSet } from '../../api/planningV1.generated'
import { tr, type PlanningLocale } from './i18n'

export function Reasons({ reasons }: { reasons: Reason[] }) {
  return <ul className="pl-reasons">{reasons.map((reason, index) => <li key={`${reason.code}-${index}`}><strong>{reason.message}</strong>
    <ul>{reason.evidence.map((ref, i) => <li key={`${ref.id}-${i}`}><code>{ref.id}@{ref.version}</code> · {ref.kind} · <span className="pl-wrap">{ref.locator}</span></li>)}</ul>
  </li>)}</ul>
}

export function FieldDataCards({ fields, weather, selected, onSelect, locale }: {
  fields: FieldProfileSnapshot[]; weather: WeatherScenarioSet; selected: string; onSelect: (id: string) => void; locale: PlanningLocale
}) {
  const t = tr(locale)
  return <section className="pl-card"><h2>{t('Data sources')}</h2>
    <p>{t('Issued')}: <time>{weather.issued_at}</time> · {weather.scenario_type} · {weather.model_version}</p>
    {!weather.weight_calibration && <p>{t('No calibrated probabilities')}</p>}
    <div className="pl-cards">{fields.map(field => {
      const grid = weather.scenarios[0]?.traces.find(r => r.field_snapshot_id === field.snapshot_id)
      const terrain = field.terrain
      const latitude = terrain?.latitude_deg ?? field.geometry.coordinates[0][0][1]
      return <article key={field.field_id} className="pl-subcard" data-selected={selected === field.field_id}>
        <button type="button" className="button subtle" aria-pressed={selected === field.field_id} onClick={() => onSelect(field.field_id)}>{field.field_id}</button>
        <p>{field.area_ha} ha · {t('Profile revision')} {field.profile_revision} · {t('Geometry revision')} {field.geometry_revision}</p>
        <p>{t('Native resolution')}: {grid ? `${grid.native_resolution_m} m · ${grid.grid_id}` : t('Unknown')}</p>
        <p>{t('Latitude')}: {latitude}° · {t('Hemisphere')}: {t(latitude > 0 ? 'North' : latitude < 0 ? 'South' : 'Equator')}</p>
        {terrain && <details><summary>{t('Terrain and sun')}</summary><p>{t('Slope')}: {terrain.slope_deg ?? t('Unknown')}° · {t('Aspect')}: {terrain.aspect_deg ?? t('Unknown')}</p>
          <p>{t('Clockwise from true north')} · {t('UTC solar time')}: {terrain.solar_time ?? t('Unknown')}</p></details>}
        <details><summary>{t('Data sources')} ({field.sources.length})</summary>{field.sources.map(source => <p key={source.id}>
          <strong>{source.provider} / {source.product}</strong> · {t(source.kind)} · {source.version}<br />
          {t('Coverage')}: {source.coverage_fraction === null ? t('Unknown') : `${source.coverage_fraction * 100}%`} · {t('Native resolution')}: {source.native_resolution_m === null ? t('Unknown') : `${source.native_resolution_m} m`}<br />
          {source.available_at} · {source.attribution}</p>)}</details>
        <Reasons reasons={field.missing_data} />
        {field.hazards.map(h => <div key={h.id}><strong>{h.kind}: {t(h.status)}</strong><p>{h.value ?? t('Unknown')} {h.unit} · {h.indicator}</p><Reasons reasons={h.reasons} /></div>)}
      </article>
    })}</div></section>
}
