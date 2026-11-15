import type { HandoffResult } from '../../../api/rotationHandoff'
import { useLocale } from '../../../shared/locale'
import { decisionBrief, type BriefCheck } from './brief'

const checks: Record<BriefCheck, [string, string]> = {
  unavailable: ['Resolve the returned model or input gap before comparing plans.', 'Усуньте зазначену прогалину моделі або даних перед порівнянням планів.'],
  land: ['Confirm sourced land evidence for this boundary. This result does not establish a sowing recommendation.', 'Підтвердьте дані про землю з джерелом для цих меж. Цей результат не встановлює рекомендацію щодо сівби.'],
  thermal: ['The assessed cycle lacks thermal growth. Review the crop and dates before considering a sowing decision.', 'В оціненому циклі немає теплових умов росту. Перегляньте культуру та дати перед рішенням про сівбу.'],
  review: ['Review the returned suitability diagnostics before using any rank.', 'Перевірте повернуті зауваження щодо придатності перед використанням рангу.'],
  resources: ['Equipment or water access checks are absent, blocked or unconfirmed. Review each plan’s resource details.', 'Перевірки техніки або доступу до води відсутні, заблоковані чи непідтверджені. Перегляньте ресурси кожного плану.'],
  priority: ['The selected criterion lacks comparable inputs. Review the missing values before ranking.', 'Для обраного критерію бракує порівнюваних даних. Перевірте відсутні значення перед ранжуванням.'],
  currencies: ['Revenue uses different currencies; these amounts cannot be compared directly.', 'Виручку подано в різних валютах; ці суми не можна порівнювати безпосередньо.'],
  costs: ['Revenue is available but margin is missing. Review included costs and currencies in the existing financial assumptions.', 'Виручка є, але маржа відсутня. Перевірте враховані витрати та валюту в наявних фінансових припущеннях.'],
  benchmark: ['Financial values include published benchmarks, not validated field-yield predictions.', 'Фінансові значення містять опубліковані довідники, а не перевірені прогнози польової врожайності.'],
  weather: ['Inspect the scenario dates and source coverage; missing weather is not a zero-risk outlook.', 'Перевірте дати сценаріїв і покриття джерел; відсутня погода не означає відсутність ризику.'],
  timing: ['The chosen calendar is not an optimal sowing date. Check near-term weather and field conditions before operations.', 'Обраний календар не є оптимальною датою сівби. Перед роботами перевірте короткий прогноз і стан поля.'],
}

export function DecisionBrief({ result, reviewInputs, openAgent }: { result: HandoffResult; reviewInputs?: () => void; openAgent?: () => void }) {
  const { language } = useLocale(), uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const brief = decisionBrief(result)
  return <aside className="handoff-brief" aria-label={text('Decision brief', 'Коротко про рішення')}>
    <div><h4>{text('What this comparison supports', 'Що дає це порівняння')}</h4>
      <p>{!brief.canCompare ? text('This result does not provide an available crop-plan comparison.', 'Цей результат не надає доступного порівняння планів культур.')
        : brief.ranked.length ? text('Compare the engine-ranked feasible options below; field accuracy remains unvalidated.', 'Порівняйте нижче здійсненні варіанти з рангом моделі; польова точність не перевірена.')
          : text('Scenario estimates are available; no crop recommendation is established.', 'Сценарні оцінки доступні; рекомендацію культури не встановлено.')}</p>
    </div>
    <div><h4>{text('What to check', 'Що перевірити')}</h4><ul>{brief.checks.map(key => <li key={key}>{checks[key][uk ? 1 : 0]}</li>)}</ul></div>
    <div><h4>{text('Next decision', 'Наступне рішення')}</h4>
      <p>{reviewInputs ? text('Keeps your current draft; does not restore saved inputs or apply a plan.', 'Зберігає поточну чернетку; не відновлює збережені входи й не застосовує план.') : text('Discuss the current field context; this saved model output is not attached.', 'Обговоріть контекст поточного поля; цей збережений результат моделі не додається.')}</p>
      <div className="handoff-brief-actions">{reviewInputs ? <button type="button" className="button" onClick={reviewInputs}>{text('Review current inputs', 'Переглянути поточні дані')}</button>
        : openAgent ? <button type="button" className="button" onClick={openAgent}>{text('Discuss this field with assistant', 'Обговорити це поле з асистентом')}</button> : null}</div>
    </div>
  </aside>
}
