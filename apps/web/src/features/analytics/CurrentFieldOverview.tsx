import { useCallback } from 'react'
import type { Field, WaterForecast } from '../../api/contracts'
import { fieldWeatherApi } from '../../api/fieldWeather'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { ErrorBox } from '../../shared/ui'
import { FieldWeatherPanel, matchesWeather } from './FieldWeatherPanel'
import { FieldWaterPanel } from './FieldWaterPanel'
import { temperatureRange } from './weatherSummary'
import { currentWater } from './currentWater'
import { waterReferenceDisplay } from '../water/waterReferenceDisplay'

export function CurrentFieldOverview({ field, forecast, season, pending, error, reload }: {
  field: Field; forecast: WaterForecast | null | undefined; season: number; pending: boolean; error?: string; reload: () => void;
}) {
  const { language, number, shortDate, date } = useLocale(), uk = language === 'uk'
  const label = (en: string, ua: string) => uk ? ua : en
  const num = (value: number | null | undefined, unit: string) => value == null ? label('Unavailable', 'Немає даних') : `${number(value, 1)} ${unit}`
  const load = useCallback((signal: AbortSignal) => fieldWeatherApi.forecast(field.id, signal), [field.id, field.boundary_revision])
  const weather = useResource(load)
  const data = matchesWeather(weather.data, field) ? weather.data : undefined
  const temperatures = temperatureRange(data?.hours ?? [])
  const today = new Date().toISOString().slice(0, 10)
  const water = currentWater(field, forecast, today)
  const display = waterReferenceDisplay(water.current?.applicability, water.outlook.kind === 'shortage', language)
  const hourlyDate = (value: string) => new Intl.DateTimeFormat(uk ? 'uk-UA' : 'en-GB', { timeZone: data!.timezone, day: 'numeric', month: 'short' }).format(new Date(value))
  return <>
    <section className="current-field-overview" aria-label={label('Current field overview', 'Поточний огляд поля')}>
      <dl className="current-field-values">
        <div className="current-temperature"><dt>{label('Temperature · 24 hours', 'Температура · 24 год')}<span>{data?.hours.length ? `${hourlyDate(data.hours[0].time)} — ${hourlyDate(data.hours.at(-1)!.time)} · ${data.timezone}` : label('Hourly forecast unavailable', 'Погодинного прогнозу немає')}</span></dt>
          <dd>{temperatures ? `${number(temperatures.min, 1)}–${number(temperatures.max, 1)} °C` : num(null, '°C')}</dd></div>
        <div className="current-rain"><dt>{label('Rain · 5 days', 'Опади · 5 днів')}<span>{data ? `${shortDate(data.requested_start)} — ${shortDate(data.requested_end)} · ${label('forecast', 'прогноз')}` : label('Forecast unavailable', 'Прогнозу немає')}</span></dt><dd>{num(data?.forecast_rain_mm, label('mm', 'мм'))}</dd></div>
        <div className="current-reserve"><dt>{label('Modeled reserve', 'Розрахунковий запас')}<span>{date(today)} · UTC · {label('day end', 'кінець дня')}</span></dt><dd>{water.first ? num(water.first.reserve_mm, label('mm', 'мм')) : label('No estimate', 'Немає оцінки')}</dd></div>
      </dl>
      {(pending || weather.pending) && <p role="status">{label('Updating current readings…', 'Оновлення поточних показників…')}</p>}
      {weather.error && <ErrorBox message={weather.error} retry={weather.reload} />}
      {error && <ErrorBox message={error} retry={reload} />}
      {weather.data && !data && <p role="status">{label('Forecast belongs to an earlier field boundary. Refresh it.', 'Прогноз відповідає попередній межі поля. Оновіть його.')} <button type="button" className="button subtle" onClick={weather.reload}>{label('Refresh', 'Оновити')}</button></p>}
      {data && !data.hours_complete && <p role="status">{label('Partial hourly readings; the range uses available temperatures only.', 'Неповні погодинні дані; діапазон лише за доступними температурами.')}</p>}
      {Boolean(data?.missing_dates.length) && <p role="status">{label('Missing forecast dates', 'Відсутні дати прогнозу')}: {data!.missing_dates.map(shortDate).join(', ')}</p>}
      {data?.hours.some(hour => hour.below_zero === true) && <p className="current-check">{label('Air below 0 °C is forecast. Check the hourly conditions before field work.', 'Прогнозується повітря нижче 0 °C. Перед роботами перевірте погодинні умови.')}</p>}
      {water.current ? <>
        <p className="current-check" role="note">{water.current.applicability?.status === 'review_required' ? label('Daily mean soil forecast (0–7 cm) ≤0 °C; freezing of the whole root zone is not established.', 'Прогноз середньодобової температури ґрунту (0–7 см) ≤0 °C; промерзання всієї кореневої зони не доведено.') : display.compact} {label('Freezing, thawing and dormancy are not modeled. Reserve is not measured liquid water or a crop-growth assessment.', 'Замерзання, відтавання і стан спокою не моделюються. Запас не є виміряною рідкою водою чи оцінкою росту культури.')}</p>
        <p className="current-next-check">{water.metrics?.firstBelow
          ? label(`Below the reference threshold from ${date(water.metrics.firstBelow)}. Review the daily water calculation before acting.`, `Нижче довідкового порога з ${date(water.metrics.firstBelow)}. Перед рішенням перевірте щоденний розрахунок води.`)
          : label('No modeled threshold crossing in the returned period; check field conditions before acting.', 'У повернутому періоді немає розрахункового перетину порога; перед рішенням перевірте стан поля.')}</p>
      </> : !pending && <p className="current-check">{label('No current water estimate. Check the recorded crop and calculate a supported forecast in Water controls below; field weather is independent of crop.', 'Немає актуальної оцінки води. Перевірте записану культуру та розрахуйте підтримуваний прогноз у керуванні водою нижче; погода поля не залежить від культури.')}</p>}
    </section>
    <div className="analytics-current-evidence">
      <FieldWeatherPanel field={field} forecast={weather} />
      <FieldWaterPanel field={field} forecast={forecast} season={season} pending={pending} error={error} reload={reload} />
    </div>
  </>
}
