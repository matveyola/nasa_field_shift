import { useLocale } from '../../shared/locale'
import { TermInfo } from '../../shared/TermInfo'
import { useStrategyLabels } from './StrategyResultCards'
import { plantingBounds, validPlantingDate } from './strategyResults'
import type { StrategyContext } from '../../api/rotationHandoff'

export function SowingDateField({ id, context, value, onChange, previousEnd, first }: {
  id: string; context: StrategyContext; value: string; onChange: (value: string) => void
  previousEnd?: string; first?: string
}) {
  const { language } = useLocale()
  const { text, date } = useStrategyLabels()
  const { min, max } = plantingBounds(context, previousEnd, first)
  const valid = !value || validPlantingDate(value, context, previousEnd, first)
  const monthName = new Intl.DateTimeFormat(language, { month: 'long', timeZone: 'UTC' })
  const month = (index: number) => monthName.format(new Date(Date.UTC(2026, index - 1, 1)))
  const months = context.min_month === context.max_month
    ? month(context.min_month)
    : `${month(context.min_month)} – ${month(context.max_month)}`
  return <>
    <div className="strategy-input-control">
      <div className="context-help-row"><label htmlFor={id}>{text('Sowing date', 'Дата сівби')}</label>
        <TermInfo term="sowing" label={text('Sowing date', 'Дата сівби')} /></div>
      <input id={id} type="date" required min={min} max={max} value={value}
        aria-describedby={`${id}-help${valid ? '' : ` ${id}-error`}`}
        aria-invalid={!valid || undefined} onInput={event => onChange(event.currentTarget.value)} />
    </div>
    <p id={`${id}-help`} className="strategy-date-help">
      {text(`Modelled sowing months: ${months}.`, `Місяці сівби для розрахунку: ${months}.`)}{' '}
      {text('This model covers spring and summer crops in the Northern Hemisphere. Autumn sowing and overwintering are outside its scope.',
        'Модель охоплює весняні й літні культури Північної півкулі. Осіння сівба й перезимівля поки поза межами розрахунку.')}
      {previousEnd && <> {text('Sow after the previous crop ends in all weather scenarios.',
        'Сівба можлива після завершення попередньої культури в усіх погодних сценаріях.')}</>}
    </p>
    {!valid && <p id={`${id}-error`} role="status">
      {text(`Choose a date in ${months} between ${date(min)} and ${date(max)}.`,
        `Оберіть дату в межах ${date(min)} – ${date(max)}. Доступні місяці: ${months}.`)}
    </p>}
    <details>
      <summary>{text('Autumn sowing examples in the southern U.S.', 'Приклади осінньої сівби на півдні США')}</summary>
      <p>{text('Regional reference windows for winter wheat grown for grain. These dates are outside the current simulation.',
        'Регіональні довідкові строки для озимої пшениці на зерно. Ці дати поза межами поточного розрахунку.')}</p>
      <ul>
        <li>{text('Georgia, Lower Coastal Plain · November 15 to December 1',
          'Джорджія, Нижня прибережна рівнина · 15 листопада – 1 грудня')}</li>
        <li>{text('Northern Florida · November 15 to December 15',
          'Північ Флориди · 15 листопада – 15 грудня')}</li>
        <li>{text('Southern Alabama · November 1 to December 1',
          'Південь Алабами · 1 листопада – 1 грудня')}</li>
      </ul>
      <a href="https://extension.uga.edu/publications/detail.html?number=B1190" target="_blank" rel="noreferrer">University of Georgia Cooperative Extension</a>
    </details>
  </>
}
