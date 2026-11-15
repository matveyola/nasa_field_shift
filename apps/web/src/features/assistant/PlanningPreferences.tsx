import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../../api/client'
import type { PlanningConstraint } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { CONSTRAINTS_CHANGED, constraintOrigin, constraintSummary, decisionStatus } from './constraintDecision'
import './assistant.css'

/**
 * The planning conditions the farmer decided on in the assistant, for this field and season. Shown so they can
 * be reviewed and withdrawn here; nothing is listed until the farmer confirms or rejects a proposal. They are
 * preferences, not measurements or farm defaults, and no comparison on this page uses them yet.
 */
export function PlanningPreferences({ fieldId, season, boundaryRevision }: { fieldId: string; season: number; boundaryRevision: number }) {
  const { t } = useLocale()
  const load = useCallback((signal: AbortSignal) => api.planningConstraints(fieldId, season, signal), [fieldId, season])
  const list = useResource(load)
  const [error, setError] = useState<string>()
  const [busy, setBusy] = useState<string>()
  const { reload } = list

  useEffect(() => {
    window.addEventListener(CONSTRAINTS_CHANGED, reload)
    return () => window.removeEventListener(CONSTRAINTS_CHANGED, reload)
  }, [reload])

  async function withdraw(decision: PlanningConstraint) {
    setBusy(decision.type)
    setError(undefined)
    try {
      // Only the exact decision shown here, for this boundary: a newer one is never removed from a stale view.
      await api.withdrawConstraint(fieldId, season, decision, boundaryRevision)
      reload()
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Something went wrong. Please try again.')
      if (problem instanceof ApiError && problem.status === 409) reload()
    } finally {
      setBusy(undefined)
    }
  }

  const constraints = list.data?.constraints ?? []
  const problem = error ?? list.error
  if (!constraints.length && !problem) return null
  return (
    <section className="panel planning-preferences" aria-labelledby="planning-preferences-heading">
      <div className="section-heading"><div>
        <h2 id="planning-preferences-heading">{t('Planning conditions from the assistant')}</h2>
        <p>{t('Your decisions for this field and season. They are preferences, not measurements or farm defaults, and rotation comparisons do not use them yet.')}</p>
      </div></div>
      {problem && <ErrorBox message={problem} retry={error ? undefined : reload} />}
      <ul className="planning-preferences-list">
        {constraints.map((constraint) => {
          const origin = constraintOrigin(constraint)
          const status = decisionStatus(constraint)
          return (
            <li key={constraint.type} className={'planning-preference planning-preference--' + status.tone}>
              <div>
                <strong>{constraintSummary(constraint, t)}</strong>
                <span className={'chip ' + (status.tone === 'confirmed' ? 'source-chip' : 'demo-chip')}>{t(status.label)}</span>
                <p>{origin.kind === 'inferred' ? t('Suggested by the assistant')
                  : origin.kind === 'farmer_words' ? t('From your words') + ': “' + origin.evidence + '”'
                  : t('Chosen by you')}</p>
              </div>
              <button className="button subtle" type="button" disabled={busy !== undefined} onClick={() => void withdraw(constraint)}>
                {busy === constraint.type ? t('Removing…') : t('Remove')}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
