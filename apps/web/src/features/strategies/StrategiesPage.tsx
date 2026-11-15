import { useState } from 'react'
import type { Field } from '../../api/contracts'
import type { StrategyMode } from '../../api/rotationHandoff'
import { useLocale } from '../../shared/locale'
import { Empty } from '../../shared/ui'
import { StrategyWorkspace } from './StrategyWorkspace'
import './strategies.css'

export function StrategiesPage({ field, fields, chooseField, openAgent }: {
  field?: Field; fields: Field[]; chooseField: (fieldId: string) => void; openAgent: () => void
}) {
  const { language } = useLocale()
  const text = (en: string, ua: string) => language === 'uk' ? ua : en
  const [mode, setMode] = useState<StrategyMode>('sequence')
  return <section className="strategies-page" aria-labelledby="strategies-title">
    <header className="strategies-heading"><div><h1 id="strategies-title">{text('What to sow next?', 'Що сіяти наступним?')}</h1></div>
      {field && <button type="button" className="button" onClick={openAgent}>{text('Discuss with assistant', 'Обговорити з асистентом')}</button>}
    </header>
    <div className="strategies-context"><label>{text('Field in focus', 'Обране поле')}<select value={field?.id ?? ''} onChange={event => chooseField(event.target.value)}>
      <option value="">{text('Choose a field', 'Оберіть поле')}</option>
      {fields.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label></div>
    {field ? <>
      <div className="strategies-workflows strategy-modes" role="group" aria-label={text('Planning workflow', 'Спосіб планування')}>
        <button type="button" className="strategies-workflow" aria-pressed={mode === 'sequence'} onClick={() => setMode('sequence')}>
          <strong>{text('Step-by-step rotation', 'Покрокова сівозміна')}</strong></button>
        <button type="button" className="strategies-workflow" aria-pressed={mode === 'compare'} onClick={() => setMode('compare')}>
          <strong>{text('Compare two crops', 'Порівняти дві культури')}</strong></button>
      </div>
      <StrategyWorkspace key={JSON.stringify([field.id, field.boundary_revision])} field={field} mode={mode} />
    </> : <Empty><p>{text('Choose a field to plan a rotation or compare crops.', 'Оберіть поле для планування сівозміни чи порівняння культур.')}</p></Empty>}
  </section>
}
