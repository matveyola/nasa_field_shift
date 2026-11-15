import { useCallback } from 'react'
import type { Field, WaterForecast } from '../../api/contracts'
import { fieldWeatherApi, type SeasonalWaterRow } from '../../api/fieldWeather'
import { useResource } from '../../shared/useResource'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { getCurrentWaterOutlook } from '../water/waterOutlook'
import { waterMetrics, seasonalGroups, range } from './waterMetrics'

export function FieldWaterPanel({ field, forecast, season, pending, error, reload }: {
  field: Field; forecast: WaterForecast | null | undefined; season: number; pending: boolean; error?: string; reload: () => void;
}) {
  const { language, number, date: fullDate, shortDate, crop } = useLocale(); const uk = language === 'uk'
  const label = (en: string, ua: string) => uk ? ua : en
  const num = (n: number | null | undefined, unit = '') => n == null ? label('No estimate', 'Немає оцінки') : `${number(n, 1)}${unit ? ' ' + unit : ''}`
  const mm = label('mm', 'мм')
  const today = new Date().toISOString().slice(0, 10)
  const supported = ['maize', 'corn', 'soy', 'soybean', 'soybeans', 'wheat', 'spring wheat', 'barley', 'sunflower', 'rapeseed', 'canola', 'peas', 'dry peas'].includes(field.crop.trim().toLowerCase())
  const scopedForecast = supported && forecast?.crop.trim().toLowerCase() === field.crop.trim().toLowerCase() ? forecast : undefined
  const outlook = getCurrentWaterOutlook(scopedForecast, field.id, today)
  const current = outlook.kind !== 'unavailable' ? forecast : undefined
  const days = outlook.kind !== 'unavailable' ? outlook.days : []
  const metrics = current ? waterMetrics(days, current.threshold_mm) : null
  const first = days[0]
  const applicability = current?.applicability
  const seasonalLoad = useCallback((signal: AbortSignal) => fieldWeatherApi.seasonal(field.id, signal), [field.id, field.boundary_revision])
  const seasonal = useResource(seasonalLoad)
  const rows = seasonal.data?.field_id === field.id && seasonal.data.geometry_revision === field.boundary_revision
    ? seasonal.data.rows.filter(row => Number(row.end_date.slice(0, 4)) === season) : []
  const groups = seasonalGroups(rows)
  const ranged = (r: ReturnType<typeof range>, unit = mm) => r ? `${num(r.median, unit)}${r.count > 1 ? ` (${num(r.min)}–${num(r.max)} ${unit}; n=${r.count})` : ''}` : label('No estimate', 'Немає оцінки')
  return <section className="field-water-block" aria-labelledby="field-water-heading">
    <header className="weather-block-heading"><div><h2 id="field-water-heading">{label('Water balance', 'Водний баланс')}</h2><p>{label('Modeled root-zone reserve using reference crop and soil assumptions.', 'Розрахунковий запас у кореневій зоні за довідковими припущеннями про культуру та ґрунт.')}</p></div>
      <button className="button" type="button" onClick={() => {reload(); seasonal.reload()}}>{label('Refresh saved results', 'Оновити збережені результати')}</button></header>
    {pending && <p role="status">{label('Loading water reserve…', 'Завантаження запасу води…')}</p>}
    {error && <ErrorBox message={error} retry={reload} />}
    {!pending && !current && <p className="analytics-empty">{label('No current water estimate. Select a supported crop and calculate a forecast in the controls below. Weather remains available independently.', 'Немає актуальної оцінки води. Оберіть підтримувану культуру й розрахуйте прогноз у керуванні нижче. Погода доступна незалежно.')}</p>}
    {current && first && metrics && <>
      <p role="note">{applicability?.status === 'review_required'
        ? label('Review required: the grid forecast has daily mean soil temperature at or below 0 °C in the shallow 0–7 cm layer. This does not establish freezing throughout the root zone.', 'Потрібна перевірка: прогноз сітки має середньодобову температуру ґрунту 0 °C або нижче у поверхневому шарі 0–7 см. Це не доводить промерзання всієї кореневої зони.')
        : !applicability
          ? label('Temperature applicability is unknown for this saved run; no shallow-soil diagnostic was stored.', 'Температурна застосовність цього збереженого розрахунку невідома: діагностику поверхневого ґрунту не збережено.')
          : label('Above-zero shallow soil temperatures do not establish crop suitability or liquid water throughout the root zone.', 'Додатна температура поверхневого ґрунту не доводить придатності для культури чи наявності рідкої води в усій кореневій зоні.')}</p>
      <p>{label('This reference bucket does not model freezing, thawing or dormancy. Its reserve and threshold are not a measurement of liquid plant-available water or a crop-growth assessment.', 'Ця довідкова балансова модель не враховує замерзання, відтавання чи стан спокою. Її запас і поріг не є вимірюванням доступної рослинам рідкої води або оцінкою росту культури.')}</p>
      <h3>{label('Water reserve today', 'Запас води сьогодні')}</h3>
      <p>{label('Today', 'Сьогодні')}: <time dateTime={today}>{fullDate(today)}</time> · UTC · {crop(current.crop)}</p>
      <div className="water-today-reading"><strong>{num(first.reserve_mm, mm)}</strong><span>{label('modeled reserve at day end', 'розрахунковий запас на кінець дня')}</span></div>
      <div className="water-reading-line"><p>{label('Threshold', 'Поріг')}: <strong>{num(current.threshold_mm, mm)}</strong></p><p>{label('Margin above threshold', 'Запас над порогом')}: <strong>{num(first.reserve_mm - current.threshold_mm, mm)}</strong></p><p>{label('Daily change', 'Зміна за добу')}: <strong>{num(first.reserve_mm - first.opening_mm, mm)}</strong></p></div>
      <h3>{label('Water movement during the day', 'Рух води за день')}</h3>
      <p>{label('Opening reserve', 'Початковий запас')}: {num(first.opening_mm, mm)} · {label('Rain', 'Опади')}: {num(first.rain_mm, mm)} · {label('Irrigation', 'Полив')}: {num(first.irrigation_mm, mm)} · {label('Modeled water use', 'Розрахункове водоспоживання')}: {num(first.water_use_mm, mm)} · {label('Drainage', 'Дренаж')}: {num(first.drainage_mm, mm)} · {label('Closing reserve', 'Кінцевий запас')}: {num(first.reserve_mm, mm)}</p>
      <h3>{label('Water reserve for the coming days', 'Запас води на найближчі дні')}</h3><p>{fullDate(days[0].date)} — {fullDate(days.at(-1)!.date)} · UTC</p>
      <svg className="water-reserve-line" viewBox="0 0 640 180" role="img" aria-label={label('Modeled daily reserve and reference threshold; exact values in the table below', 'Розрахунковий щоденний запас і довідковий поріг; точні значення в таблиці нижче')}>
        <text x="8" y="16">{mm}</text>
        {[0, current.capacity_mm / 2, current.capacity_mm].map(value => <g key={value}>
          <line x1="60" x2="620" y1={160 - 130 * value / Math.max(1, current.capacity_mm)} y2={160 - 130 * value / Math.max(1, current.capacity_mm)} stroke="#ddd" />
          <text x="52" y={164 - 130 * value / Math.max(1, current.capacity_mm)} textAnchor="end">{num(value)}</text>
        </g>)}
        <line x1="60" x2="620" y1={160 - 130 * current.threshold_mm / Math.max(1, current.capacity_mm)} y2={160 - 130 * current.threshold_mm / Math.max(1, current.capacity_mm)} stroke="#666" strokeDasharray="6 4" />
        <text x="620" y={Math.max(18, 154 - 130 * current.threshold_mm / Math.max(1, current.capacity_mm))} textAnchor="end">{label('Threshold', 'Поріг')}: {num(current.threshold_mm, mm)}</text>
        <polyline fill="none" stroke="currentColor" strokeWidth="3" points={days.map((d,i) => `${60+560*i/Math.max(1,days.length-1)},${160-130*d.reserve_mm/Math.max(1,current.capacity_mm)}`).join(' ')} />
        <text x="60" y="177">{fullDate(days[0].date)}</text><text x="620" y="177" textAnchor="end">{fullDate(days.at(-1)!.date)}</text>
      </svg>
      <dl className="water-result-lines">
        <div><dt>{label('First day below threshold', 'Перший день нижче порога')}</dt><dd>{metrics.firstBelow ? shortDate(metrics.firstBelow) : label('None in this period', 'Немає в цьому періоді')}</dd></div>
        <div><dt>{label('Minimum reserve', 'Мінімальний запас')}</dt><dd>{num(metrics.minimum?.reserve_mm, mm)} · {metrics.minimum && shortDate(metrics.minimum.date)}</dd></div>
        <div><dt>{label('Days below threshold', 'Днів нижче порога')}</dt><dd>{metrics.stressDays}</dd></div>
        <div><dt>{label('Longest continuous shortage', 'Найдовший безперервний дефіцит')}</dt><dd>{metrics.longest} {label('days', 'днів')} {metrics.longestStart && `· ${shortDate(metrics.longestStart)} — ${shortDate(metrics.longestEnd!)}`}</dd></div>
        <div><dt>{label('Flowering-period stress', 'Стрес у період цвітіння')}</dt><dd>{label('No estimate — no confirmed flowering window in this forecast', 'Немає оцінки — у прогнозі немає підтвердженого вікна цвітіння')}</dd></div>
      </dl>
      <p>{label('Period totals', 'Суми за період')}: {label('rain', 'опади')} {num(metrics.rain, mm)} · {label('irrigation', 'полив')} {num(metrics.irrigation, mm)} · {label('water use', 'витрата води')} {num(metrics.use, mm)} · {label('drainage', 'дренаж')} {num(metrics.drainage, mm)}</p>
      <details className="analytics-disclosure"><summary>{label('Daily calculation and assumptions', 'Щоденний розрахунок і припущення')}</summary><div className="analytics-details-body">
        <p>{label('Capacity', 'Місткість')}: {num(current.capacity_mm, mm)} · {label('Root depth', 'Глибина коріння')}: {num(current.root_depth_m, label('m', 'м'))}</p>
        <p>{label('Forecast initialized', 'Прогноз ініціалізовано')}: {current.initialized_at} · {label('Calculated', 'Розраховано')}: {current.generated_at}</p>
        <p>{current.provider} · {current.physical_model_version} · <a href={current.source_url} target="_blank" rel="noreferrer">{label('Source', 'Джерело')}</a></p>
        {applicability && <div>
          <p>{label('Shallow soil temperature forecast', 'Прогноз температури поверхневого ґрунту')} · 0–7 cm · °C · {label('daily mean', 'середньодобова')} · {applicability.source_variable} · {applicability.initialized_at}</p>
          <ul>{applicability.soil_temperature.map(day => <li key={day.date}><time dateTime={day.date}>{fullDate(day.date)}</time>: {num(day.mean_c, '°C')}</li>)}</ul>
        </div>}
        <div className="analytics-table-scroll"><table><thead><tr>{[label('Date','Дата'),label('Opening','На початок'),label('Closing','На кінець'),label('Rain','Опади'),label('Irrigation','Полив'),label('Use','Витрата'),label('Drainage','Дренаж'),label('Source rain','Опади джерела'),'ET₀'].map(x=><th key={x}>{x} {x!==label('Date','Дата') && `(${mm})`}</th>)}</tr></thead>
          <tbody>{days.map(d=><tr key={d.date}><td>{shortDate(d.date)}</td>{[d.opening_mm,d.reserve_mm,d.rain_mm,d.irrigation_mm,d.water_use_mm,d.drainage_mm,d.source_rain_mm,d.reference_et0_mm].map((v,i)=><td key={i}>{num(v)}</td>)}</tr>)}</tbody></table></div>
        {current.limitations.map(s=><p key={s}>{s}</p>)}
      </div></details>
    </>}
    <details className="analytics-disclosure"><summary>{label('Water for the season', 'Вода за сезон')} · {season}</summary><div className="analytics-details-body">
      <p>{label('Saved model executions, not validated field accuracy or drought probability. Separate executions, plans and periods remain separate; median and range summarize scenarios within one execution.', 'Збережені виконання моделі, не перевірена точність для поля чи ймовірність посухи. Виконання, плани та періоди не змішуються; медіана й діапазон описують сценарії одного виконання.')}</p>
      {seasonal.pending && <p role="status">{label('Loading saved scenarios…','Завантаження збережених сценаріїв…')}</p>}
      {seasonal.error && <ErrorBox message={seasonal.error} retry={seasonal.reload} />}
      {!seasonal.pending && !groups.length && <p>{label('No estimate — no saved matching scenario results.', 'Немає оцінки — немає відповідних збережених результатів сценарію.')}</p>}
      {groups.map(group=><article key={group.key}><h3>{crop(group.first.crop_id)} · {group.first.plan_id}</h3><p>{fullDate(group.first.start_date)} — {fullDate(group.first.end_date)}</p>
        <dl className="water-result-lines">{([
          ['demand_mm',label('Demand','Потреба'),mm],['use_mm',label('Modeled water use','Розрахункове водоспоживання'),mm],['deficit_mm',label('Water deficit','Дефіцит води'),mm],
          ['drought_days',label('Drought days','Дні посухи'),label('days','днів')],['crop_days',label('Crop period','Період культури'),label('days','днів')],
          ['opening_mm',label('Opening reserve','Початковий запас'),mm],['closing_mm',label('Closing reserve','Кінцевий запас'),mm],['irrigation_mm',label('Irrigation','Полив'),mm],['drainage_mm',label('Drainage','Дренаж'),mm],
          ['waterlogging_days',label('Waterlogging days','Дні перезволоження'),label('days','днів')],['water_stress_days',label('Total water-stress days','Усього днів водного стресу'),label('days','днів')],
        ] as const).map(([metric,title,unit])=><div key={metric}><dt>{title}</dt><dd>{ranged(range(group.runs.map(r=>r[metric] as number|null)),unit)}</dd></div>)}
          <div><dt>{label('Deficit / demand','Дефіцит / потреба')}</dt><dd>{ranged(group.deficitPercent,'%')}</dd></div><div><dt>{label('Drought days / crop days','Дні посухи / дні культури')}</dt><dd>{ranged(group.droughtPercent,'%')}</dd></div></dl>
        <details><summary>{label('Run provenance','Походження виконань')}</summary>{group.runs.map((r:SeasonalWaterRow)=><p key={r.job_id+':'+r.scenario_id}>{r.job_id} · {r.scenario_id} · {r.data_kind} · {r.evidence_sha256}</p>)}</details>
      </article>)}
    </div></details>
  </section>
}
