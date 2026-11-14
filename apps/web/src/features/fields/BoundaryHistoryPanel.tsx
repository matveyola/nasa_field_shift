import { useCallback, useState } from 'react'
import { ClockIcon, PencilSquareIcon } from '@heroicons/react/24/outline'
import type { Field, Polygon } from '../../api/contracts'
import { boundaryApi } from '../../api/boundaries'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { ErrorBox } from '../../shared/ui'
import { useAccess } from '../access/AccessProvider'

export function BoundaryHistoryPanel({ field, season, onEdit }: {
  field: Field; season: number; onEdit: (geometry: Polygon, effectiveSeason: number) => void;
}) {
  const { language, number, date } = useLocale()
  const uk = language === 'uk'
  const { canEdit } = useAccess()
  const [opened, setOpened] = useState(false)
  const [targetSeason, setTargetSeason] = useState(season)
  const load = useCallback((signal: AbortSignal) => opened ? boundaryApi.history(field.id, signal) : Promise.resolve(undefined), [field.id, field.boundary_revision, opened])
  const history = useResource(load)
  return <details className="field-context-details boundary-history" onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary><ClockIcon aria-hidden="true" /><span>{uk ? 'Історія меж' : 'Boundary history'}</span></summary>
    <div className="field-area-strip">
      <p className="analysis-description">{uk ? 'Кожне збереження зберігає попередній контур. Відновлення створює нову версію. Сезонні розрахунки використовують відповідні межі.' : 'Every save keeps the previous contour. Restoring creates a new version. Seasonal analysis uses the applicable boundary.'}</p>
      <label className="control"><span>{uk ? 'Діє із сезону' : 'Effective from season'}</span><input type="number" min="2000" max={season} value={targetSeason} onChange={(event) => setTargetSeason(Number(event.target.value))} /></label>
      {canEdit && <button type="button" className="button subtle" disabled={!Number.isInteger(targetSeason) || targetSeason < 2000 || targetSeason > season} onClick={() => onEdit(field.geometry, targetSeason)}>
        <PencilSquareIcon aria-hidden="true" />{uk ? 'Намалювати межу сезону' : 'Edit seasonal boundary'}</button>}
      {history.error && <ErrorBox message={history.error} retry={history.reload} />}
      {history.data?.map((version) => <div className="boundary-version" key={version.id}>
        <strong>{uk ? 'Із сезону' : 'From season'} {version.effective_season} · v{version.revision}</strong>
        <span>{number(version.area_ha, 2)} {uk ? 'га' : 'ha'} · {date(version.created_at)}</span>
        {canEdit && <button type="button" className="button subtle" disabled={!Number.isInteger(targetSeason) || targetSeason < 2000 || targetSeason > season} onClick={() => onEdit(version.geometry, targetSeason)}>{uk ? 'Переглянути й відновити' : 'Review and restore'}</button>}
      </div>)}
      {!history.pending && !history.data?.length && <p>{uk ? 'Історія з’явиться після першого редагування.' : 'History starts with the first boundary edit.'}</p>}
      <p className="analysis-footnote">{uk ? 'Для років до першої записаної межі використовується найраніший доступний контур. Додайте точні межі минулих сезонів, якщо вони відрізнялися.' : 'Years before the first recorded boundary reuse the earliest contour. Add accurate past-season boundaries when they differed.'}</p>
    </div>
  </details>
}
