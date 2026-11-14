import { useCallback } from 'react'
import { ArrowRightIcon } from '@heroicons/react/24/outline'
import { api } from '../../api/client'
import type { RotationPlan } from '../../api/contracts'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { Empty, ErrorBox, Loading } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { rotationPlanColors } from './RotationComparison'

export function RotationYieldPanel({
  fieldId, season, plans,
}: { fieldId: string; season: number; plans: RotationPlan[] }) {
  const { t, crop, number } = useLocale()
  const load = useCallback((signal: AbortSignal) => api.rotationYield(fieldId, season, signal), [fieldId, season])
  const resource = useResource(load)
  const data = resource.data?.field_id === fieldId && resource.data.season === season ? resource.data : undefined
  const estimates = data?.plans.flatMap((entry) => {
    const plan = plans.find(({ id }) => id === entry.id)
    return plan ? entry.estimates.map((estimate) => ({ ...estimate, planId: plan.id, planName: plan.name })) : []
  }) ?? []
  const hasEstimate = (estimate: typeof estimates[number]): estimate is typeof estimate & { change_t_ha: number } =>
    data?.status !== 'unavailable' && typeof estimate.change_t_ha === 'number' && Number.isFinite(estimate.change_t_ha)
  const supported = estimates.filter(hasEstimate)
  const unavailable = estimates.filter((estimate) => !hasEstimate(estimate))
  const unavailableGroups = new Map<string, typeof estimates>()
  for (const estimate of unavailable) {
    const reason = estimate.reason || 'Estimate unavailable'
    unavailableGroups.set(reason, [...(unavailableGroups.get(reason) ?? []), estimate])
  }
  const extent = Math.max(1, ...supported.flatMap(({ change_t_ha, lower_t_ha, upper_t_ha }) =>
    [change_t_ha, lower_t_ha, upper_t_ha].map((value) => Math.abs(value ?? 0))))
  const position = (value: number) => 50 + 50 * value / extent

  return (
    <section className="rotation-yield-section" aria-labelledby="rotation-yield-heading">
      <div className="section-heading">
        <div>
          <h2 id="rotation-yield-heading">{t('Yield change')}</h2>
          <p>{t('Compared with continuous maize')}</p>
        </div>
        {data?.status === 'validated' && <span className="chip source-chip">{t('Field-validated model')}</span>}
        {data?.status === 'experimental' && <span className="chip demo-chip">{t('Experimental model')}</span>}
      </div>
      {resource.pending && <Loading text={t('Loading yield estimates…')} />}
      {resource.error && <ErrorBox message={resource.error} retry={resource.reload} />}
      {data?.status === 'experimental' && (
        <p className="rotation-yield-caution">{t('Experimental field-trial estimate for research use only.')}</p>
      )}
      {data && supported.length === 0 && <Empty>{t('No rotation yield estimate is available for this field.')}</Empty>}
      {supported.length > 0 && (
        <div className="rotation-yield-charts">
          {supported.map((estimate) => (
              <figure className="rotation-yield-chart" key={estimate.planId + ':' + estimate.year}>
                <figcaption><strong>{t(estimate.planName)}</strong><span>{estimate.year}</span></figcaption>
                  <span className="rotation-yield-sequence">
                    {estimate.previous_crop && <>
                      <span className="rotation-yield-crop"><CropIcon crop={estimate.previous_crop} />{crop(estimate.previous_crop)}</span>
                      <ArrowRightIcon aria-hidden="true" /><span className="sr-only">{t('then')}</span>
                    </>}
                    <span className="rotation-yield-crop"><CropIcon crop={estimate.crop} />{crop(estimate.crop)}</span>
                  </span>
                    <strong className="rotation-yield-value">{number(estimate.change_t_ha, 2, true)} <small>{t('t/ha')}</small></strong>
                    <div className="rotation-yield-track" aria-hidden="true">
                      <i className="rotation-yield-bar" data-negative={estimate.change_t_ha < 0}
                        style={{ left: Math.min(50, position(estimate.change_t_ha)) + '%',
                          width: Math.abs(position(estimate.change_t_ha) - 50) + '%',
                          background: rotationPlanColors[plans.findIndex(({ id }) => id === estimate.planId) % rotationPlanColors.length] }} />
                      {estimate.lower_t_ha != null && estimate.upper_t_ha != null &&
                        <i className="rotation-yield-interval" style={{ left: position(estimate.lower_t_ha) + '%',
                          width: position(estimate.upper_t_ha) - position(estimate.lower_t_ha) + '%' }} />}
                    </div>
                    <div className="rotation-yield-axis" aria-hidden="true"><span>{number(-extent, 1)}</span><span>0</span><span>{number(extent, 1, true)}</span></div>
                    {estimate.lower_t_ha != null && estimate.upper_t_ha != null && <small className="rotation-yield-range">
                      {t('Prediction interval')} · {number(estimate.lower_t_ha, 2, true)} – {number(estimate.upper_t_ha, 2, true)} {t('t/ha')}
                    </small>}
              </figure>
            ))}
        </div>
      )}
      {unavailable.length > 0 && <details className="rotation-yield-unavailable" open={supported.length === 0}>
        <summary>{t('Years without an estimate')} <span>{unavailable.length}</span></summary>
        <div>
          {Array.from(unavailableGroups, ([reason, entries]) => <section key={reason}>
            <p>{t(reason)}</p>
            <ul>{entries.map((entry) => <li key={entry.planId + ':' + entry.year}>
              <strong>{t(entry.planName)} · {entry.year}</strong>
              <span className="rotation-yield-crop"><CropIcon crop={entry.crop} />{crop(entry.crop)}</span>
            </li>)}</ul>
          </section>)}
        </div>
      </details>}
      <details className="rotation-yield-sources">
        <summary>{t('Yield model and sources')}</summary>
        <div>
          <p>{t('Estimated grain yield change relative to continuous maize in rainfed field trials.')}</p>
          <div className="analysis-source-links">
            {data?.source_url && <a href={data.source_url} target="_blank" rel="noreferrer">{t('Field trial data')}</a>}
            {data?.training_sites != null && <span>{t('Training sites')} {data.training_sites}</span>}
            {data?.model_version && <span>{t('Model')} {data.model_version}</span>}
          </div>
        </div>
      </details>
    </section>
  )
}
