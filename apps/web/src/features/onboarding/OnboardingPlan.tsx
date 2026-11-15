import type { OnboardingSetup } from '../../api/onboarding'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import './fieldOnboarding.css'

export function OnboardingPlan({ setup }: { setup: OnboardingSetup }) {
  const { language, crop } = useLocale()
  const uk = language === 'uk'
  const { demo_plan: plan, inputs: answers } = setup
  if (!plan) return null
  const priority = answers.priority === 'water' ? (uk ? 'Менша залежність від опадів' : 'Less dependence on rainfall')
    : answers.priority === 'soil' ? (uk ? 'Відновлення ґрунту' : 'Soil health')
    : (uk ? 'Мінімум змін' : 'Fewer changes')
  const irrigation = answers.irrigation == null ? (uk ? 'Невідомо' : 'Unknown')
    : answers.irrigation ? (uk ? 'Доступне' : 'Available') : (uk ? 'Недоступне' : 'Unavailable')
  return (
    <section className="card onboarding-plan" aria-labelledby="onboarding-plan-title">
      <span className="chip demo-chip">{uk ? 'Демонстраційний план' : 'Demo plan'}</span>
      <h2 id="onboarding-plan-title">{uk ? 'Стратегії для цього поля' : 'Strategies for this field'}</h2>
      <div className="onboarding-plan-variants">
        {[plan.crops, ...(plan.alternatives ?? [])].map((sequence, index) => (
          <article className="onboarding-plan-variant" key={sequence.map(({ crop }) => crop).join('/')}>
            <h3>{uk ? 'Варіант' : 'Option'} {index + 1}</h3>
            <div className="rotation-crop-timeline">
              {sequence.map(({ year, crop: name }) => <div className="rotation-crop-stop" key={year}>
                <span className="rotation-crop-year">{year}</span>
                <span className="rotation-crop-illustration"><CropIcon crop={name} /></span>
                <strong>{crop(name)}</strong>
              </div>)}
            </div>
          </article>
        ))}
      </div>
      <dl className="onboarding-plan-meta">
        <div><dt>{uk ? 'Доступні культури' : 'Available crops'}</dt><dd>{answers.allowed_crops?.map(crop).join(', ')}</dd></div>
        <div><dt>{uk ? 'Історія культур' : 'Crop history'}</dt><dd>
          {answers.history?.filter(({ crop }) => crop).map(({ season, crop: name }) => season + ' · ' + crop(name ?? '')).join(', ')
            || (uk ? 'Невідома' : 'Unknown')}
        </dd></div>
        <div><dt>{uk ? 'Пріоритет' : 'Priority'}</dt><dd>{priority}</dd></div>
        <div><dt>{uk ? 'Зрошення' : 'Irrigation'}</dt><dd>{irrigation}</dd></div>
      </dl>
      <p className="onboarding-plan-caption">{uk
        ? 'Приклад послідовності з обраних культур. Прогнози врожайності, водного ризику й стану ґрунту ще не розраховані.'
        : 'An example sequence using your selected crops. Yield, water risk, and soil health are not yet estimated.'}</p>
    </section>
  )
}
