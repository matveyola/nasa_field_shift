import { useCallback, useState } from 'react'
import { analyticsApi } from '../../../api/analytics'
import type { Field } from '../../../api/contracts'
import { ComparisonBars } from '../../../shared/DataCharts'
import { useLocale } from '../../../shared/locale'
import { ErrorBox } from '../../../shared/ui'
import { useResource } from '../../../shared/useResource'
import { ZonePreview } from './ZonePreview'
import { compareHistory, type SceneLayer } from './zones'
import './precision.css'

type Props = {
  field: Field
  season: number
  fingerprint: string
  layer?: SceneLayer | null
}

export function NdviHistoryPanel(props: Props) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  return <section className="ndvi-history">
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t('NDVI patterns across seasons')}</summary>
      {open && (props.layer?.cells.length && props.fingerprint
        ? <HistoryComparison key={props.field.id + ':' + props.fingerprint + ':' + props.layer.date} {...props} layer={props.layer} />
        : <p className="analysis-empty">{t('Select a processed NDVI scene to work with its zones.')}</p>)}
    </details>
  </section>
}

function HistoryComparison({ field, season, fingerprint, layer }: Props & { layer: SceneLayer }) {
  const { t, number, date, language } = useLocale()
  const month = layer.date.slice(5, 7)
  const load = useCallback(async (signal: AbortSignal) => {
    const years = Array.from({ length: Math.min(4, season - 2017) }, (_, index) => season - index - 1)
    return Promise.all(years.map(async (year) => {
      const result = await analyticsApi.vegetation(field.id, year, signal, false)
      if (result.boundary_revision !== field.boundary_revision || result.geometry_fingerprint !== fingerprint) {
        return { year, reason: 'Different analyzed boundary' } as const
      }
      const observation = result.observations.find((item) => item.date.slice(5, 7) === month)
      if (!observation) return { year, reason: 'No saved clear scene for this month' } as const
      const snapshot = await analyticsApi.vegetationScene(field.id, year, observation.date, observation.scene_id, signal)
      if (!snapshot?.heterogeneity.cells.length || snapshot.geometry_fingerprint !== fingerprint
        || snapshot.boundary_revision !== field.boundary_revision || snapshot.observation.date !== observation.date) {
        return { year, reason: 'No comparable map for this scene' } as const
      }
      return { year, date: observation.date, layer: snapshot.heterogeneity } as const
    }))
  }, [field.id, field.boundary_revision, season, fingerprint, month])
  const history = useResource(load)
  const older = history.data?.flatMap((item) => 'layer' in item && item.layer ? [item.layer] : []) ?? []
  const summary = compareHistory([layer, ...older])
  const matchedArea = summary ? summary.persistent + summary.typical + summary.changing : 0
  const monthName = new Intl.DateTimeFormat(language === 'uk' ? 'uk-UA' : 'en', { month: 'long', timeZone: 'UTC' })
    .format(new Date(layer.date))
  return <>
    <p className="analysis-description">{t('Same calendar month, current scene and up to four earlier seasons with the same analyzed boundary.')} · {monthName}</p>
    {history.error && <ErrorBox message={history.error} retry={history.reload} />}
    {history.pending && <p className="analysis-empty" role="status">{t('Loading comparable scenes…')}</p>}
    {history.data && <>
      <ul className="precision-history-list">
        <li><span>{season}</span><time dateTime={layer.date}>{date(layer.date)}</time></li>
        {history.data.map((item) => <li key={item.year}><span>{item.year}</span>
          {item.date ? <time dateTime={item.date}>{date(item.date)}</time> : <span>{t(item.reason ?? 'No comparable map for this scene')}</span>}
        </li>)}
      </ul>
      {summary ? <>
        {matchedArea > 0
          ? <p className="analysis-description">{number(100 * matchedArea / (matchedArea + summary.unmatched), 1)}% {t('of the selected scene’s mapped area is comparable')}</p>
          : <p className="analysis-empty">{t('The saved grids have no cells in common.')}</p>}
        <ComparisonBars groups={[{ label: t('NDVI pattern area'), unit: t('ha'), values: [
          { label: t('Persistent lower or higher NDVI'), value: summary.persistent },
          { label: t('Consistently typical NDVI'), value: summary.typical },
          { label: t('Changed NDVI category'), value: summary.changing },
          { label: t('No matching cell across seasons'), value: summary.unmatched },
        ] }]} />
        {summary.stableCells.length > 0 && <ZonePreview boundary={field.geometry} cells={summary.stableCells} />}
        <p className="analysis-footnote">{t('A cell must match across all compared grids. Values describe relative vegetation patterns.')} · {number(older.length + 1, 0)} {t('seasons compared')}</p>
      </> : <p className="analysis-empty">{t('Process this month in at least three seasons with matching boundaries to compare patterns.')}</p>}
    </>}
  </>
}
