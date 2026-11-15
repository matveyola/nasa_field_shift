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

const checkTitles: Record<BriefCheck, [string, string]> = {
  unavailable: ['Model or inputs unavailable', 'Модель або дані недоступні'],
  land: ['Land evidence needs confirmation', 'Потрібне підтвердження даних про землю'],
  thermal: ['No thermal growth in this cycle', 'У цьому циклі немає теплових умов росту'],
  review: ['Suitability needs review', 'Потрібна перевірка придатності'],
  resources: ['Review equipment and water access', 'Перевірте техніку та доступ до води'],
  priority: ['Missing inputs for ranking', 'Бракує даних для ранжування'],
  currencies: ['Different currencies cannot be compared', 'Різні валюти не можна порівнювати'],
  costs: ['Missing costs prevent a margin estimate', 'Без витрат немає оцінки маржі'],
  benchmark: ['Financial figures use benchmarks', 'Фінансові оцінки містять довідникові дані'],
  weather: ['Weather coverage is incomplete', 'Покриття погодою неповне'],
  timing: ['Calendar dates need a field check', 'Календарні дати потребують перевірки на полі'],
}

export function DecisionBrief({ result, reviewInputs, openAgent }: { result: HandoffResult; reviewInputs?: () => void; openAgent?: () => void }) {
  const { language } = useLocale(), uk = language === 'uk'
  const text = (en: string, ua: string) => uk ? ua : en
  const brief = decisionBrief(result)
  return <aside className="handoff-brief" aria-label={text('Decision brief', 'Коротко про рішення')}>
    <div className="handoff-brief-lead"><div><h4>{text('What this comparison supports', 'Що дає це порівняння')}</h4>
      <p>{!brief.canCompare ? text('This result does not provide an available crop-plan comparison.', 'Цей результат не надає доступного порівняння планів культур.')
        : brief.ranked.length ? text('Compare the engine-ranked feasible options below; field accuracy remains unvalidated.', 'Порівняйте нижче здійсненні варіанти з рангом моделі; польова точність не перевірена.')
          : text('Scenario estimates are available; no crop recommendation is established.', 'Сценарні оцінки доступні; рекомендацію культури не встановлено.')}</p>
    </div>
      <div className="handoff-brief-actions">{reviewInputs ? <button type="button" className="button" onClick={reviewInputs}>{text('Review current inputs', 'Переглянути поточні дані')}</button>
        : openAgent ? <button type="button" className="button" onClick={openAgent}>{text('Discuss this field with assistant', 'Обговорити це поле з асистентом')}</button> : null}</div>
    </div>
    <ul className="handoff-brief-checks">{brief.checks.map(key => <li key={key}><details><summary>{checkTitles[key][uk ? 1 : 0]}</summary><p>{checks[key][uk ? 1 : 0]}</p></details></li>)}</ul>
    <details><summary>{text('Next decision', 'Наступне рішення')}</summary><p>{reviewInputs ? text('Review the assumptions in your current draft for a new comparison.', 'Уточніть припущення в поточній чернетці, щоб розрахувати новий варіант.') : text('Discuss the current field’s conditions and missing information with the assistant.', 'Обговоріть стан і відсутні дані поточного поля з асистентом.')}</p></details>
  </aside>
}
