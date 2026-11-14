import { ArrowRightIcon } from '@heroicons/react/24/outline'
import type { VegetationResult } from '../../api/analytics'
import { useLocale } from '../../shared/locale'
import { vegetationInsight } from '../fields/vegetationInsights'

export function FieldVegetationSignal({ data, openAnalysis }: { data?: VegetationResult; openAnalysis: () => void }) {
  const { t, number, shortDate } = useLocale()
  const signal = vegetationInsight(data, new Date().toISOString().slice(0, 10))
  return <section className={'field-vegetation-signal' + (signal?.inspect ? ' is-inspect' : '')} aria-label={t('Vegetation signal')}>
    <h3>{t(!signal ? 'Check the seasonal vegetation pattern' : signal.stale ? 'Satellite observation needs updating'
      : signal.inspect ? 'Inspect the lower-NDVI zones' : 'Vegetation variation')}</h3>
    <p>{signal ? <>{number(signal.lowAreaPercent)}% {t('of observed area has lower NDVI')} · {shortDate(signal.date)}.
      {' '}{t(signal.stale ? 'This observation is over 30 days old.' : 'Compare the zones with field observations before deciding on an intervention.')}</>
      : t('Use clear Sentinel-2 observations to see changes within this field.')}</p>
    {signal?.inspect && <p>{t('Inspection cue: at least 10% of observed area is below the field median by 0.15 NDVI. This is a transparent rule, not a disease model.')}</p>}
    <button type="button" onClick={openAnalysis}>{t('Open vegetation analysis')}<ArrowRightIcon aria-hidden="true" /></button>
  </section>
}
