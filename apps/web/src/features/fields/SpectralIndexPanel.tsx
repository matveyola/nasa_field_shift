import { useId, useState } from 'react'
import { ArrowDownTrayIcon, MapIcon } from '@heroicons/react/24/outline'
import type { SpectralIndex, VegetationObservation } from '../../api/analytics'
import { useLocale } from '../../shared/locale'
import { spectralDefinitions, spectralMean } from './spectralIndices'

export function SpectralIndexPanel({ observations, selected, onSelect, onMap }: {
  observations: VegetationObservation[]; selected?: VegetationObservation;
  onSelect: (date: string) => void; onMap: (index: SpectralIndex) => void;
}) {
  const { language, number, shortDate } = useLocale()
  const uk = language === 'uk'
  const [index, setIndex] = useState<SpectralIndex>('ndre')
  const title = useId()
  const definition = spectralDefinitions[index]
  const points = observations.flatMap((point) => {
    const value = spectralMean(point, index)
    return value == null || !Number.isFinite(value) ? [] : [{ date: point.date, value }]
  })
  const value = selected && spectralMean(selected, index)
  const min = Math.min(0, ...points.map((point) => point.value))
  const max = Math.max(min + .1, ...points.map((point) => point.value))
  const x = (day: string) => 37 + (Date.parse(day) - Date.parse(points[0]?.date ?? day)) / Math.max(1, Date.parse(points.at(-1)?.date ?? day) - Date.parse(points[0]?.date ?? day)) * 264
  const y = (v: number) => 145 - (v - min) / (max - min) * 120
  function download() {
    const rows = ['date,index,mean,source', ...points.map((point) => `${point.date},${index.toUpperCase()},${point.value},Sentinel-2 L2A`)]
    const url = URL.createObjectURL(new Blob([rows.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = `${index}-observations.csv`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <section className="spectral-panel" aria-label={uk ? 'Супутникові індекси' : 'Satellite indices'}>
    <header className="field-analysis-heading"><h3>{uk ? 'Супутникові індекси' : 'Satellite indices'}</h3></header>
    <label className="control"><span>{uk ? 'Індекс' : 'Index'}</span><select value={index} onChange={(event) => setIndex(event.target.value as SpectralIndex)}>
      {(Object.keys(spectralDefinitions) as SpectralIndex[]).map((key) => <option key={key} value={key}>{key.toUpperCase()} · {spectralDefinitions[key][uk ? 'uk' : 'en']}</option>)}
    </select></label>
    <p className="analysis-description">{definition[uk ? 'uk' : 'en']}</p>
    <div className="ndvi-current"><strong>{value == null ? '—' : number(value, 3)}<small>{index.toUpperCase()}</small></strong>
      <div>{selected && <time dateTime={selected.date}>{shortDate(selected.date)}</time>}</div></div>
    {selected && index !== 'ndvi' && index !== 'msavi' && selected.indices?.[index] && <p className="analysis-description">
      {number(selected.indices[index]!.valid_percent, 1)}% {uk ? 'покриття для цього індексу' : 'coverage for this index'}
    </p>}
    {points.length > 0 ? <>
      <figure className="seasonal-ndvi-chart"><svg viewBox="0 0 320 172" role="img" aria-labelledby={title}>
        <title id={title}>{index.toUpperCase()} · {uk ? 'Сезонна зміна середнього значення' : 'Seasonal mean observations'}</title>
        {[min, (min + max) / 2, max].map((tick) => <g key={tick}><line x1="37" x2="304" y1={y(tick)} y2={y(tick)} className="forecast-gridline" /><text x="31" y={y(tick) + 3} textAnchor="end">{number(tick, 2)}</text></g>)}
        {points.map((point, i) => {
          const previous = points[i - 1]
          const adjacent = previous && Number(point.date.slice(5, 7)) === Number(previous.date.slice(5, 7)) + 1
          return <g key={point.date}>{adjacent && <line x1={x(previous.date)} x2={x(point.date)} y1={y(previous.value)} y2={y(point.value)} style={{ stroke: 'var(--series-1)' }} strokeWidth="2" />}
            <circle cx={x(point.date)} cy={y(point.value)} r="4" style={point.date === selected?.date ? { fill: '#fff', stroke: 'var(--series-1)', strokeWidth: 2.2 } : { fill: 'var(--series-1)' }} />
            <circle cx={x(point.date)} cy={y(point.value)} r="10" fill="transparent" onClick={() => onSelect(point.date)} />
          </g>
        })}
        <text x="37" y="165">{shortDate(points[0].date)}</text><text x="304" y="165" textAnchor="end">{shortDate(points.at(-1)!.date)}</text>
      </svg></figure>
      <div className="ndvi-observation-dates">{points.map((point) => <button type="button" key={point.date} aria-pressed={point.date === selected?.date} onClick={() => onSelect(point.date)}>{shortDate(point.date)}</button>)}</div>
    </> : <p className="analysis-empty">{uk ? 'Цей індекс ще не обчислений. Оновіть супутниковий аналіз — будуть завантажені потрібні спектральні канали.' : 'This index has not been processed. Update satellite analysis to load the required spectral bands.'}</p>}
    <div className="field-editor-actions"><button type="button" className="button subtle" disabled={value == null} onClick={() => onMap(index)}><MapIcon aria-hidden="true" />{uk ? 'Показати на карті' : 'Show on map'}</button>
      <button type="button" className="button subtle" disabled={!points.length} onClick={download}><ArrowDownTrayIcon aria-hidden="true" />CSV</button></div>
    <details className="analysis-quality"><summary>{uk ? 'Формула та обмеження' : 'Formula and limitations'}</summary>
      <p className="spectral-formula">{definition.formula}</p>
      <p>{uk ? 'Відбивна здатність Sentinel-2 L2A, спільна сітка 20 м. Хмари й невалідні канали маскуються. Відсутні пікселі залишаються прозорими.' : 'Sentinel-2 L2A reflectance on a common 20 m grid. Clouds and invalid bands are masked. Missing pixels remain transparent.'}</p>
      <p>{uk ? 'NDMI оцінює спектральний контраст вологи рослин, NDWI — відкритої води. Вони не вимірюють вологість ґрунту. PRI потребує вузьких каналів 531/570 нм, яких Sentinel-2 не має. Калібрований SMI тут не обчислюється.' : 'NDMI describes canopy moisture contrast and NDWI open water. Neither measures soil moisture. PRI requires narrow 531/570 nm bands absent from Sentinel-2. A calibrated SMI is not calculated here.'}</p>
      <a href="https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel/sentinel-2/" target="_blank" rel="noreferrer">Sentinel Hub · {uk ? 'Джерела формул' : 'Formula references'}</a>
    </details>
  </section>
}
