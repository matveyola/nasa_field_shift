import type { ReactNode } from 'react'
import { ArrowPathIcon, ExclamationCircleIcon } from '@heroicons/react/24/outline'
import { scenarios, type Scenario } from '../api/contracts'
import { LanguageSelect, useLocale } from './locale'
import { HeaderActions } from './HeaderActions'

export function PageHeading({ title, detail, season, dataLabel = 'Demo data' }: {
  title: string
  detail?: string
  season?: number
  dataLabel?: string | null
}) {
  const { t } = useLocale()
  return (
    <header className="page-heading">
      <div>
        <h1>{t(title)}</h1>
        {detail && <p>{detail}</p>}
      </div>
      <div className="heading-tags">
        {dataLabel && <span className={'chip ' + (dataLabel === 'Demo data' ? 'demo-chip' : 'source-chip')}>{t(dataLabel)}</span>}
        <LanguageSelect />
        {season !== undefined && <span className="chip season-chip">{t('Season')} {season}</span>}
        <HeaderActions />
      </div>
    </header>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state" role="status">{children}</div>
}

export function Loading({ text = 'Loading…' }: { text?: string }) {
  const { t } = useLocale()
  return <Empty><ArrowPathIcon className="spin" aria-hidden="true" />{t(text)}</Empty>
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  const { t } = useLocale()
  return (
    <div className="error-box" role="alert">
      <ExclamationCircleIcon aria-hidden="true" />
      <span>{t(message)}</span>
      {retry && <button className="button subtle" type="button" onClick={retry}>{t('Try again')}</button>}
    </div>
  )
}

export function ScenarioSelect({ value, onChange }: { value: Scenario; onChange: (value: Scenario) => void }) {
  const { t } = useLocale()
  return (
    <label className="control">
      <span>{t('Scenario')}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as Scenario)}>
        {scenarios.map((scenario) => <option key={scenario.id} value={scenario.id}>{t(scenario.label)}</option>)}
      </select>
    </label>
  )
}
