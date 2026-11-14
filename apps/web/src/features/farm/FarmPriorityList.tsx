import { ArrowPathIcon, ArrowRightIcon, CheckCircleIcon, ClockIcon, ExclamationTriangleIcon, PencilSquareIcon } from '@heroicons/react/24/outline'
import { useCallback } from 'react'
import type { Field } from '../../api/contracts'
import { api } from '../../api/client'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { Loading } from '../../shared/ui'
import { type FieldWaterPriority } from './fieldPriority'
import { loadFarmForecasts } from './loadFarmForecasts'
import { loadFarmVegetation, rankCombinedPriorities } from './farmVegetation'
import { analyticsApi } from '../../api/analytics'
import './farmPriority.css'

const missingLabels = {
  missing: 'No saved water forecast', expired: 'Forecast expired', stale: 'Forecast needs an update',
  invalid: 'Forecast data could not be verified', incomplete: 'No forecast for today', request_failed: 'Could not load the forecast',
} as const

export function FarmPriorityList({ fields, openField, openWater, openVegetation, recordObservation, season }: {
  fields: Field[]
  openField: (fieldId: string) => void
  openWater?: (fieldId: string, date?: string) => void
  openVegetation?: (fieldId: string) => void
  recordObservation: (fieldId: string) => void
  season: number
}) {
  const { t, number, shortDate } = useLocale()
  const today = new Date().toISOString().slice(0, 10)
  const load = useCallback(async (signal: AbortSignal) => {
    const forecasts = await loadFarmForecasts(fields, today, signal, api.waterForecast)
    const vegetation = await loadFarmVegetation(fields, season, today, signal,
      (fieldId, year, abort) => analyticsApi.vegetation(fieldId, year, abort, false))
    return { forecasts, vegetation }
  }, [fields, today, season])
  const resource = useResource(load, 5 * 60 * 1000)
  const entries = rankCombinedPriorities(resource.data?.forecasts ?? [], resource.data?.vegetation ?? {})
  const shortages = entries.filter((entry) => entry.priority.kind === 'shortage')
  const unknown = entries.filter((entry) => entry.priority.kind === 'unavailable').length
  const riskArea = shortages.reduce((sum, { field }) => sum + field.area_ha, 0)
  if (!fields.length) return null
  return (
    <section className="farm-section farm-priorities" aria-labelledby="farm-priorities-heading">
      <div className="farm-section-heading">
        <div><h3 id="farm-priorities-heading">{t('Where to look first')}</h3><p>{t('Water forecasts and satellite inspection cues. Open a field to inspect the evidence.')}</p></div>
        <button type="button" className="icon-button" aria-label={t('Refresh field priorities')} disabled={resource.pending} onClick={resource.reload}><ArrowPathIcon aria-hidden="true" /></button>
      </div>
      {resource.pending && !resource.data ? <Loading text="Checking field forecasts…" /> : <>
        <div className="farm-priority-summary" aria-live="polite">
          <span data-risk={shortages.length > 0}><ExclamationTriangleIcon aria-hidden="true" /><strong>{number(shortages.length, 0)}</strong> {t('with a water signal')}{shortages.length > 0 && <> · {number(riskArea)} {t('ha')}</>}</span>
          <span><ClockIcon aria-hidden="true" /><strong>{number(unknown, 0)}</strong> {t('need fresh data')}</span>
        </div>
        <ol className="farm-priority-list">
          {entries.map(({ field, priority }) => <li key={field.id} data-status={priority.kind}>
            <div className="farm-priority-field"><CropIcon crop={field.crop} /><strong>{field.name}</strong><small>{number(field.area_ha)} {t('ha')}</small></div>
            <div className="farm-priority-evidence"><PrioritySummary priority={priority} />
              {resource.data?.vegetation[field.id] && <button type="button" className="farm-vegetation-cue"
                onClick={() => (openVegetation ?? openField)(field.id)}>
                <span>{t(resource.data.vegetation[field.id]!.stale ? 'Satellite observation needs updating' : resource.data.vegetation[field.id]!.inspect ? 'Inspect the lower-NDVI zones' : 'Lower-NDVI zones')}</span>
                <small>{number(resource.data.vegetation[field.id]!.lowAreaPercent)}% {t('of observed area')} · {shortDate(resource.data.vegetation[field.id]!.date)}</small>
              </button>}
              {(priority.kind === 'shortage' || resource.data?.vegetation[field.id]?.inspect) &&
                <button type="button" className="farm-record-observation" onClick={() => recordObservation(field.id)}
                  aria-label={`${t('Record observation')} · ${field.name}`}>
                  <PencilSquareIcon aria-hidden="true" />{t('Record observation')}
                </button>}
            </div>
            <div className="farm-priority-action">
              {priority.kind !== 'unavailable' && <PrioritySparkline priority={priority} />}
              <button className="button subtle" type="button" onClick={() => {
                if (openWater) openWater(field.id, priority.kind === 'unavailable' ? undefined : priority.firstStressDate ?? priority.startDate)
                else openField(field.id)
              }} aria-label={`${t(priority.kind === 'unavailable' ? 'Open forecast' : 'Inspect signal')} · ${field.name}`}>
                {t(priority.kind === 'unavailable' ? 'Open forecast' : 'Inspect signal')}<ArrowRightIcon aria-hidden="true" />
              </button>
            </div>
          </li>)}
        </ol>
        {resource.error && <p role="alert">{t(resource.error)}</p>}
        <p className="farm-priority-note">{t('Water stress comes first, then current satellite inspection cues, then missing water data. Missing data does not mean low risk.')}</p>
      </>}
    </section>
  )
}

function PrioritySummary({ priority }: { priority: FieldWaterPriority }) {
  const { t, number, shortDate } = useLocale()
  if (priority.kind === 'unavailable') return <div className="farm-priority-message"><ClockIcon aria-hidden="true" /><div><strong>{t(missingLabels[priority.reason])}</strong><span>{t('Update the forecast before assessing water risk.')}</span></div></div>
  const shortage = priority.kind === 'shortage'
  return <div className="farm-priority-message">
    {shortage ? <ExclamationTriangleIcon aria-hidden="true" /> : <CheckCircleIcon aria-hidden="true" />}
    <div><strong>{shortage ? <>{t('Below the stress threshold from')} {shortDate(priority.firstStressDate!)}</> : t('No shortage within this forecast')}</strong>
      <span>{t('Minimum reserve')} {number(priority.minimumMm)} {t('mm')} · {t('Stress threshold')} {number(priority.thresholdMm)} {t('mm')}</span>
      <small>{shortDate(priority.startDate)}–{shortDate(priority.endDate)} · {t('Forecast run')} {shortDate(priority.initializedAt)} · UTC</small>
    </div>
  </div>
}

function PrioritySparkline({ priority }: { priority: Exclude<FieldWaterPriority, { kind: 'unavailable' }> }) {
  const min = Math.min(priority.thresholdMm, ...priority.values)
  const max = Math.max(...priority.values, min + 1)
  const y = (value: number) => 38 - (value - min) / (max - min) * 30
  const points = priority.values.map((value, index) => `${4 + index / Math.max(1, priority.values.length - 1) * 100},${y(value)}`).join(' ')
  return <svg className="farm-priority-sparkline" viewBox="0 0 108 44" aria-hidden="true">
    <line x1="3" x2="105" y1={y(priority.thresholdMm)} y2={y(priority.thresholdMm)} stroke="#b42318" strokeDasharray="3 3" />
    <polyline points={points} fill="none" stroke={priority.kind === 'shortage' ? '#b42318' : '#0d0d0d'} strokeWidth="2" strokeLinejoin="round" />
  </svg>
}
