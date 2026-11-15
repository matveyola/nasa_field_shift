import { useRef, useState, type KeyboardEvent } from 'react'
import type { Field } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { Empty } from '../../shared/ui'
import { StrategyWorkspace, type StrategyTab } from './StrategyWorkspace'
import './strategies.css'

const tabs = ['date', 'scenarios'] as const

export function StrategiesPage({ field, fields, chooseField }: {
  field?: Field; fields: Field[]; chooseField: (fieldId: string) => void
}) {
  const { language } = useLocale()
  const text = (en: string, ua: string) => language === 'uk' ? ua : en
  const [tab, setTab] = useState<StrategyTab>('date')
  const buttons = useRef<Partial<Record<StrategyTab, HTMLButtonElement>>>({})
  const labels = { date: text('Sowing date', 'Дата сівби'), scenarios: text('Scenarios', 'Сценарії') }

  function navigate(event: KeyboardEvent<HTMLButtonElement>, current: StrategyTab) {
    let next: StrategyTab
    switch (event.key) {
      case 'Home': next = 'date'; break
      case 'End': next = 'scenarios'; break
      case 'ArrowLeft':
      case 'ArrowRight': next = current === 'date' ? 'scenarios' : 'date'; break
      default: return
    }
    event.preventDefault()
    setTab(next)
    buttons.current[next]?.focus()
  }

  return <section className="strategies-page" aria-labelledby="strategies-title">
    <header className="strategies-heading"><h1 id="strategies-title">{text('Strategies', 'Стратегії')}</h1></header>
    <div className="strategies-context"><label htmlFor="strategy-field">{text('Field in focus', 'Обране поле')}<select id="strategy-field" value={field?.id ?? ''} onChange={event => chooseField(event.target.value)}>
      <option value="">{text('Choose a field', 'Оберіть поле')}</option>
      {fields.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label></div>
    <div className="strategies-workflows strategy-modes" role="tablist" aria-label={text('Strategies', 'Стратегії')}>
      {tabs.map(item => <button type="button" className="strategies-workflow" key={item} role="tab"
        id={`strategy-tab-${item}`} aria-controls={`strategy-panel-${item}`} aria-selected={tab === item}
        tabIndex={tab === item ? 0 : -1} ref={element => { if (element) buttons.current[item] = element }}
        onClick={() => setTab(item)} onKeyDown={event => navigate(event, item)}>
        <strong>{labels[item]}</strong>
      </button>)}
    </div>
    {field ? <StrategyWorkspace key={JSON.stringify([field.id, field.boundary_revision])} field={field} tab={tab} />
      : tabs.map(item => <div key={item} id={`strategy-panel-${item}`} role="tabpanel" aria-labelledby={`strategy-tab-${item}`} hidden={item !== tab}>
        <Empty><p>{text('Choose a field to continue.', 'Оберіть поле, щоб продовжити.')}</p></Empty>
      </div>)}
  </section>
}
