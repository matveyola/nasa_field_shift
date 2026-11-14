import { chartColors } from '../../shared/DataCharts'
import { useLocale } from '../../shared/locale'

export const rotationPlanColors = chartColors

export function RotationComparison({ groups }: {
  groups: { label: string; unit: string; values: { label: string; value: number | null }[] }[]
}) {
  const { t, number } = useLocale()
  return <div className="rotation-comparison-charts">
    {groups.map(({ label, unit, values }) => {
      const max = Math.max(1, ...values.map(({ value }) => value ?? 0))
      return <figure className="rotation-comparison-chart" key={label}>
        <figcaption>{label}</figcaption>
        <ul>{values.map((item, index) => <li key={item.label}>
          <span className="rotation-bar-label"><i style={{ background: rotationPlanColors[index % rotationPlanColors.length] }} aria-hidden="true" />{item.label}</span>
          <strong>{item.value === null ? t('Not supplied') : <>{number(item.value)} <small>{unit}</small></>}</strong>
          <div className="rotation-comparison-track" aria-hidden="true">
            {item.value !== null && <i style={{ width: Math.max(0, item.value) / max * 100 + '%', background: rotationPlanColors[index % rotationPlanColors.length] }} />}
          </div>
        </li>)}</ul>
      </figure>
    })}
  </div>
}
