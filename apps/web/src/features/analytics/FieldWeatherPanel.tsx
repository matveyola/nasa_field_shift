import { useCallback } from 'react'
import type { Field } from '../../api/contracts'
import { fieldWeatherApi, type WeatherScope, type RainHistory } from '../../api/fieldWeather'
import { useResource } from '../../shared/useResource'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'

export function matchesWeather(data: WeatherScope | null | undefined, field: Field) {
  return data?.field_id === field.id && data.boundary_revision === field.boundary_revision
}
export function WeatherSources({ data }: { data: WeatherScope }) {
  const { language } = useLocale(); const uk = language === 'uk'
  return <details className="analytics-disclosure"><summary>{uk ? 'Джерела та точність' : 'Sources and precision'}</summary>
    <div className="analytics-details-body">{data.sources.map(s => <p key={s.url}><a href={s.url} target="_blank" rel="noreferrer">{s.provider}</a> · {s.retrieved_at}<br />{s.description}</p>)}
      <p>{uk ? 'Сіткові дані поблизу поля, не вимірювання на полі.' : 'Grid data near the field, not measurements on the field.'} {data.latitude}, {data.longitude}</p>
      <p>{uk ? 'Ревізія межі' : 'Boundary revision'} {data.boundary_revision}</p></div></details>
}
export function FieldWeatherPanel({ field, periodDays, setPeriodDays }: { field: Field; periodDays: 7 | 30; setPeriodDays: (days: 7 | 30) => void }) {
  const { language, number, date: fullDate, shortDate } = useLocale(); const uk = language === 'uk'
  const label = (en: string, ua: string) => uk ? ua : en
  const num = (n: number | null | undefined, unit = '') => n == null ? label('Unavailable', 'Немає даних') : `${number(n, 1)}${unit ? ' ' + unit : ''}`
  const forecastLoad = useCallback((signal: AbortSignal) => fieldWeatherApi.forecast(field.id, signal), [field.id, field.boundary_revision])
  const historyLoad = useCallback((signal: AbortSignal) => fieldWeatherApi.history(field.id, false, signal), [field.id, field.boundary_revision])
  const forecast = useResource(forecastLoad)
  const archive = useResource(historyLoad)
  const current = matchesWeather(forecast.data, field) ? forecast.data : undefined
  const recent = matchesWeather(archive.data, field) ? archive.data : undefined
  const end = recent?.available_end
  const baselineLoad = useCallback((signal: AbortSignal) => end ? fieldWeatherApi.history(field.id, true, signal) : Promise.resolve(null), [field.id, field.boundary_revision, end])
  const baseline = useResource(baselineLoad)
  const history: RainHistory | undefined = matchesWeather(baseline.data, field) && baseline.data?.available_end === end ? baseline.data ?? undefined : recent
  return <section className="field-weather-block" aria-labelledby="field-weather-heading">
    <header className="weather-block-heading"><div><h2 id="field-weather-heading">{label('Field weather', 'Погода поля')}</h2><p>{label('Weather does not require a selected crop.', 'Погода доступна без обраної культури.')}</p></div>
      <button className="button" type="button" onClick={() => {forecast.reload(); archive.reload(); baseline.reload()}}>{label('Refresh', 'Оновити')}</button></header>
    {forecast.pending && <p role="status">{label('Loading forecast…', 'Завантаження прогнозу…')}</p>}
    {forecast.data && !current && <p role="status">{label('Forecast belongs to an earlier field boundary. Refresh it.', 'Прогноз відповідає попередній межі поля. Оновіть його.')}</p>}
    {forecast.error && <ErrorBox message={forecast.error} retry={forecast.reload} />}
    {current && <>
      <h3>{label('Weather for 24 hours', 'Погода на 24 години')}</h3><p className="analytics-meta">{current.timezone} · {current.hours[0] && new Intl.DateTimeFormat(uk ? 'uk-UA' : 'en-GB', {timeZone: current.timezone, dateStyle:'short',timeStyle:'short'}).format(new Date(current.hours[0].time))} — {current.hours.at(-1) && new Intl.DateTimeFormat(uk ? 'uk-UA' : 'en-GB', {timeZone: current.timezone, dateStyle:'short',timeStyle:'short'}).format(new Date(current.hours.at(-1)!.time))}</p>
      {!current.hours_complete && <p role="status">{label('Some forecast hours or measurements are missing.', 'Частина годин або показників прогнозу відсутня.')}</p>}
      {current.hours.some(h => h.below_zero === true) && <p className="analytics-caution">{label('Below 0 °C is forecast. Check the hours below.', 'Прогнозується температура нижче 0 °C. Перевірте години нижче.')}</p>}
      <div className="weather-hour-strip" role="list" aria-label={label('Hourly weather', 'Погодинна погода')}>{current.hours.map(h => <div role="listitem" className={h.below_zero ? 'weather-hour below-zero' : 'weather-hour'} key={h.time}>
        <time dateTime={h.time}>{new Intl.DateTimeFormat(uk ? 'uk-UA' : 'en-GB', { timeZone: current.timezone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(h.time))}</time>
        <strong>{num(h.temperature_c, '°C')}</strong><span>{label('Rain', 'Опади')}: {num(h.rain_mm, label('mm', 'мм'))}</span><span>{label('Gusts', 'Пориви')}: {num(h.gust_ms, label('m/s', 'м/с'))}</span></div>)}</div>
      <h3>{label('Rain forecast for 5 days', 'Прогноз опадів на 5 днів')}</h3><p>{fullDate(current.requested_start)} — {fullDate(current.requested_end)} · {label('Forecast', 'Прогноз')}</p>
      <p className="weather-rain-total"><strong>{num(current.forecast_rain_mm, label('mm', 'мм'))}</strong> {label('total precipitation', 'опадів загалом')}</p>
      <div className="weather-daily-list">{current.forecast_days.map(d => <div key={d.date}><time dateTime={d.date}>{shortDate(d.date)}</time><strong>{num(d.rain_mm, label('mm', 'мм'))}</strong><span>{label('Rain chance', 'Імовірність опадів')}: {num(d.rain_probability_percent, '%')}</span></div>)}</div>
      <p>{label('Most precipitation', 'Найбільше опадів')}: {current.wettest_dates.length ? current.wettest_dates.map(shortDate).join(', ') : label('Unavailable', 'Немає даних')}</p>
      <p>{label('Lowest daily maximum rain chance', 'Найменша денна максимальна ймовірність опадів')}: {current.lowest_probability_dates.length ? current.lowest_probability_dates.map(shortDate).join(', ') : label('Unavailable', 'Немає даних')}</p>
      {current.missing_dates.length > 0 && <p role="status">{label('Missing forecast dates', 'Відсутні дати прогнозу')}: {current.missing_dates.map(shortDate).join(', ')}</p>}
      <WeatherSources data={current} />
    </>}
    <h3>{label('Rainfall already recorded', 'Опади за минулий період')}</h3>
    {archive.pending && <p role="status">{label('Loading completed days…', 'Завантаження завершених днів…')}</p>}
    {archive.data && !recent && <p role="status">{label('Archive response does not match this field boundary. Refresh it.', 'Архівна відповідь не відповідає цій межі поля. Оновіть її.')}</p>}
    {archive.error && <ErrorBox message={archive.error} retry={archive.reload} />}
    {history && <>
      <p>{label('Archive, completed UTC days through', 'Архів, завершені дні UTC до')} {history.available_end ? fullDate(history.available_end) : label('unavailable date', 'невідомої дати')}{history.archive_lag_days ? ` · ${label('Archive lag', 'Затримка архіву')}: ${history.archive_lag_days} ${label('days', 'днів')}` : ''}</p>
      <div className="rainfall-period-selector" role="group" aria-label={label('Archive period', 'Архівний період')}>{([7, 30] as const).map(length => <button key={length} type="button" className="button" aria-pressed={periodDays === length} onClick={() => setPeriodDays(length)}>{length} {label('days', 'днів')}</button>)}</div>
      <div className="rainfall-periods">{history.periods.filter(p => p.length_days === periodDays).map(p => <article key={p.length_days}><h4>{p.length_days} {label('completed days', 'завершених днів')}</h4><p>{fullDate(p.start)} — {fullDate(p.end)}</p><strong>{num(p.rain_mm, label('mm', 'мм'))}</strong>
        <p>{label('Days with less than 1 mm of rain', 'Днів з опадами менше 1 мм')}: {num(p.dry_days)}</p>
        {!p.complete && <p role="status">{label('Incomplete period; no comparison.', 'Неповний період; порівняння недоступне.')} {p.missing_dates.length} {label('missing days', 'відсутніх днів')}</p>}
        <p>{label('Typical median', 'Типова медіана')}: {num(p.typical_rain_mm, label('mm', 'мм'))} · {p.typical_periods.length}/10 {label('complete periods', 'повних періодів')}</p>
        {p.complete && p.difference_mm !== null && <p>{label('Difference from typical', 'Різниця з типовим')}: {p.difference_mm > 0 ? '+' : ''}{num(p.difference_mm, label('mm', 'мм'))}</p>}
        {p.typical_status === 'not_requested' && baseline.pending && <p role="status">{label('Calculating the typical period…', 'Розрахунок типового періоду…')}</p>}
        <details><summary>{label('Reference periods', 'Референтні періоди')}</summary>{p.typical_periods.map(s => <p key={s.end}>{s.start} — {s.end}: {num(s.rain_mm, label('mm', 'мм'))}</p>)}</details>
      </article>)}</div>
      {baseline.error && <ErrorBox message={baseline.error} retry={baseline.reload} />}
      <WeatherSources data={history} /><details className="analytics-disclosure"><summary>{label('Comparison method', 'Метод порівняння')}</summary><p>{label('Median of complete periods in the previous ten years. Same end month/day; February 29 becomes February 28 in non-leap years. Every sample retains exactly 7 or 30 days. Internal gaps are not skipped.', 'Медіана повних періодів попередніх десяти років. Той самий кінцевий місяць і день; 29 лютого стає 28 лютого у невисокосні роки. Кожен період має рівно 7 або 30 днів. Внутрішні пропуски не оминаються.')}</p></details>
    </>}
  </section>
}
