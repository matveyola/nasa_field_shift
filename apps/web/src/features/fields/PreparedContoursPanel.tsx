import { useCallback, useEffect, useState } from 'react'
import { MapIcon } from '@heroicons/react/24/outline'
import { boundaryApi, type PreparedContour } from '../../api/boundaries'
import type { Position } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { ErrorBox } from '../../shared/ui'

export type MapWindow = { west: number; south: number; east: number; north: number }

export function PreparedContoursPanel({ viewport, onContours, onFocus }: {
  viewport?: MapWindow; onContours: (contours: PreparedContour[]) => void; onFocus: (center: Position) => void;
}) {
  const { language, number } = useLocale()
  const uk = language === 'uk'
  const [opened, setOpened] = useState(false)
  const [window, setWindow] = useState<MapWindow>()
  useEffect(() => {
    const timer = setTimeout(() => setWindow(viewport), 350)
    return () => clearTimeout(timer)
  }, [viewport])
  const windowKey = JSON.stringify(window)
  const small = Boolean(window && window.east - window.west <= .2 && window.north - window.south <= .2)
  const loadCoverage = useCallback((signal: AbortSignal) => opened ? boundaryApi.coverage(signal) : Promise.resolve(undefined), [opened])
  const coverage = useResource(loadCoverage)
  const loadContours = useCallback((signal: AbortSignal) => opened && small && window
    ? boundaryApi.contours(window, signal).then((data) => ({ ...data, windowKey })) : Promise.resolve(undefined), [opened, small, windowKey])
  const results = useResource(loadContours)
  const current = results.data?.windowKey === windowKey ? results.data : undefined
  useEffect(() => { onContours(opened && current ? current.contours : []) }, [opened, current, onContours])
  useEffect(() => () => onContours([]), [onContours])
  return <details className="field-context-details" onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary><MapIcon aria-hidden="true" /><span>{uk ? 'Готові контури України' : 'Prepared Ukraine contours'}</span></summary>
    <div className="field-area-strip">
      <p className="analysis-description">{uk ? 'Клацніть готовий контур на карті, підправте вершини та збережіть як поле. Контури 2023 року потребують перевірки на актуальному знімку.' : 'Click a prepared contour on the map, adjust its vertices, then save as a field. The 2023 boundaries need checking against current imagery.'}</p>
      {coverage.data?.map((area) => <div className="boundary-version" key={`${area.region}:${area.year}`}>
        <strong>{area.region} · {area.year}</strong><span>{number(area.count, 0)} {uk ? 'імпортованих контурів' : 'imported contours'} · {area.license}</span>
        <button type="button" className="button subtle" onClick={() => onFocus([(area.west + area.east) / 2, (area.south + area.north) / 2])}>{uk ? 'Перейти до покриття' : 'Go to coverage'}</button>
        <a href={area.source_url} target="_blank" rel="noreferrer">NASA Harvest · Sadeh et al.</a>
      </div>)}
      {coverage.data?.length === 0 && <p>{uk ? 'Каталог ще не імпортовано для цього сервера.' : 'No prepared catalogue has been imported on this server.'}</p>}
      <p role="status">{!small ? (uk ? 'Наблизьте карту, щоб побачити контури.' : 'Zoom in to show prepared contours.') : results.pending ? (uk ? 'Завантаження контурів…' : 'Loading contours…') : current ? `${current.contours.length} ${uk ? 'контурів у вікні карти' : 'contours in the map window'}` : ''}</p>
      {current?.truncated && <p>{uk ? 'Показано перші 200. Наблизьте карту для решти.' : 'Showing the first 200. Zoom in for the remaining contours.'}</p>}
      {(coverage.error || results.error) && <ErrorBox message={coverage.error ?? results.error!} retry={results.reload} />}
      <p className="analysis-footnote">{uk ? 'Покриття обмежене імпортованими ділянками. Контури спрощено не більш як на 20 м зі зміною площі до 2%; складні полігони з отворами пропущено.' : 'Coverage is limited to imported areas. Contours are simplified by at most 20 m with under 2% area change; complex polygons with holes are omitted.'}</p>
    </div>
  </details>
}
