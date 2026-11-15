import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeftIcon, ArrowRightIcon, ArrowUpIcon, ChatBubbleLeftEllipsisIcon, CheckIcon } from '@heroicons/react/24/outline'
import type { PlanningCrop } from '../../api/rotationPlanning'
import { useLocale } from '../../shared/locale'
import { ErrorBox } from '../../shared/ui'
import { onboardingCrops, parseCropAnswer, parseHistoryAnswer, parseIrrigationAnswer, type OnboardingAnswers, type OnboardingPriority } from './onboardingAnswers'
import './onboardingChat.css'

export type { OnboardingAnswers, OnboardingPriority } from './onboardingAnswers'

type Step = 'history' | 'allowed_crops' | 'irrigation' | 'priority'
const steps: Step[] = ['history', 'allowed_crops', 'irrigation', 'priority']
const priorities: OnboardingPriority[] = ['water', 'soil', 'low_change']

type Props = {
  fieldName: string
  season: number
  answers: OnboardingAnswers
  onAnswersChange: (answers: OnboardingAnswers) => void
  onBack: () => void
  onComplete: () => void
  busy?: boolean
  error?: string
}

export function OnboardingChat({ fieldName, season, answers, onAnswersChange, onBack, onComplete, busy, error }: Props) {
  const { language, crop } = useLocale()
  const uk = language === 'uk'
  const [editing, setEditing] = useState<Step>()
  const [historyYearOverride, setHistoryYearOverride] = useState<number>()
  const [selected, setSelected] = useState<string[]>()
  const [text, setText] = useState('')
  const [feedback, setFeedback] = useState<{ message: string; error: boolean }>()
  const active = editing ?? steps.find((step) => answers[step] === undefined)
  const promptRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { promptRef.current?.focus() }, [active])
  const currentSelection = selected ?? answers.allowed_crops ?? []
  const previousSeason = season - 1
  const historyYear = historyYearOverride ?? previousSeason
  const previousCrop = answers.history?.find((item) => item.season === previousSeason)?.crop
  const olderCrop = answers.history?.find((item) => item.season === previousSeason - 1)?.crop
  const assistant = uk ? 'Помічник' : 'Assistant'
  const you = uk ? 'Ви' : 'You'
  const cropLabel = (value: string) => uk && value === 'Peas' ? 'Горох' : crop(value)

  const labels: Record<Step, string> = uk
    ? { history: 'Попередня культура', allowed_crops: 'Доступні культури', irrigation: 'Полив', priority: 'Ваш пріоритет' }
    : { history: 'Previous crop', allowed_crops: 'Available crops', irrigation: 'Irrigation', priority: 'Your priority' }
  const historyQuestion = (year: number) => uk
    ? `Що росло на цьому полі в сезоні ${year}?`
    : `What grew in this field in the ${year} season?`
  const questions: Record<Step, string> = uk
    ? {
        history: historyQuestion(previousSeason),
        allowed_crops: 'Які культури ви можете вирощувати на цьому полі? Оберіть принаймні одну.',
        irrigation: 'Чи можете ви поливати це поле?',
        priority: 'Що найважливіше для вас у наступних сезонах?',
      }
    : {
        history: historyQuestion(previousSeason),
        allowed_crops: 'Which crops can you grow in this field? Choose at least one.',
        irrigation: 'Can you irrigate this field?',
        priority: 'What matters most to you over the next seasons?',
      }
  const goals: Record<OnboardingPriority, string> = uk
    ? { water: 'Менша залежність від опадів', soil: 'Відновлення ґрунту', low_change: 'Мінімум змін у господарстві' }
    : { water: 'Less dependence on rainfall', soil: 'Improve soil health', low_change: 'Fewer changes to farm practices' }
  const irrigationLabel = (value: boolean | null | undefined) => value == null
    ? (uk ? 'Не знаю' : 'Not sure')
    : value ? (uk ? 'Так' : 'Yes') : (uk ? 'Ні' : 'No')
  const answerLabels: Record<Step, string> = {
    history: [
      `${previousSeason} · ${previousCrop ? cropLabel(previousCrop) : (uk ? 'Не пам’ятаю' : 'I don’t remember')}`,
      ...(olderCrop ? [`${previousSeason - 1} · ${cropLabel(olderCrop)}`] : []),
    ].join(', '),
    allowed_crops: answers.allowed_crops?.map(cropLabel).join(', ') ?? '',
    irrigation: irrigationLabel(answers.irrigation),
    priority: answers.priority ? goals[answers.priority] : '',
  }

  function applyAnswer(update: Partial<OnboardingAnswers>) {
    onAnswersChange({ ...answers, ...update })
    setEditing(undefined)
    setHistoryYearOverride(undefined)
    setText('')
    setFeedback(undefined)
  }

  function setHistory(value: PlanningCrop | null) {
    const otherYears = answers.history?.filter(({ season }) => season !== historyYear) ?? []
    applyAnswer({ history: value ? [...otherYears, { season: historyYear, crop: value }] : otherYears })
  }

  function edit(step: Step, year?: number) {
    setEditing(step)
    setHistoryYearOverride(year)
    setSelected(answers.allowed_crops)
    setText('')
    setFeedback(undefined)
  }

  function toggleCrop(value: PlanningCrop) {
    setSelected(currentSelection.includes(value)
      ? currentSelection.filter((item) => item !== value)
      : [...currentSelection, value])
    setFeedback(undefined)
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !text.trim()) return
    if (active === 'history') {
      const value = parseHistoryAnswer(text)
      if (value !== undefined) { setHistory(value); return }
    }
    if (active === 'allowed_crops') {
      const value = parseCropAnswer(text)
      if (value) {
        const next = value.exclude
          ? (selected ?? answers.allowed_crops ?? onboardingCrops).filter((item) => !value.crops.some((removed) => item === removed))
          : value.crops
        setSelected(next)
        setText('')
        const listed = value.crops.map(cropLabel).join(', ')
        const response = value.exclude
          ? (uk ? 'Виключено: ' : 'Excluded: ') + listed + '. '
          : ''
        setFeedback({
          message: response + (uk ? 'Перевірте й підтвердьте вибір культур.' : 'Review and confirm your crop selection.'),
          error: false,
        })
        return
      }
    }
    if (active === 'irrigation') {
      const value = parseIrrigationAnswer(text)
      if (value !== undefined) { applyAnswer({ irrigation: value }); return }
    }
    if (active === 'priority') {
      const normalized = text.trim().toLowerCase().replace(/[.!?]+$/, '')
      const value = priorities.find((item) => goals[item].toLowerCase() === normalized)
      if (value) { applyAnswer({ priority: value }); return }
    }
    setFeedback({
      message: uk ? 'Не вдалося розпізнати відповідь. Скористайтеся одним із варіантів вище.' : 'That answer wasn’t recognized. Please choose an option above.',
      error: true,
    })
  }

  const activeIndex = active === undefined ? steps.length : steps.indexOf(active)
  const completed = steps.slice(0, activeIndex).filter((step) => answers[step] !== undefined)
  const placeholder = active === 'allowed_crops'
    ? (uk ? 'Наприклад, «Сою не вирощую»' : 'For example, “I don’t grow soy”')
    : (uk ? 'Введіть відповідь' : 'Type an answer')

  return (
    <section className="onboarding-chat" aria-labelledby="onboarding-chat-title">
      <header className="onboarding-chat-header">
        <span className="onboarding-chat-mark"><ChatBubbleLeftEllipsisIcon aria-hidden="true" /></span>
        <div>
          <h2 id="onboarding-chat-title">{assistant}</h2>
          <p>{fieldName}</p>
        </div>
      </header>
      <div className="onboarding-chat-messages">
        <p className="onboarding-chat-prompt">
          {uk ? 'Кілька коротких запитань допоможуть підготувати варіанти сівозміни.' : 'A few short questions will help shape the rotation options.'}
        </p>
        {completed.map((step) => (
          <div className="onboarding-chat-exchange" key={step}>
            <p className="onboarding-chat-prompt"><span className="sr-only">{assistant}: </span>{questions[step]}</p>
            <div className="onboarding-chat-answer">
              <span><span className="sr-only">{you}: </span>{answerLabels[step]}</span>
              <button type="button" className="onboarding-answer-edit" disabled={busy} onClick={() => edit(step)}
                aria-label={uk ? `Змінити: ${labels[step]}` : `Edit ${labels[step]}`}>
                {uk ? 'Змінити' : 'Edit'}
              </button>
              {step === 'history' && previousSeason > 2000 && (
                <button type="button" className="onboarding-answer-edit" disabled={busy}
                  onClick={() => edit(step, previousSeason - 1)}>
                  {olderCrop
                    ? (uk ? `Змінити ${previousSeason - 1}` : `Edit ${previousSeason - 1}`)
                    : (uk ? 'Додати сезон' : 'Add a season')}
                </button>
              )}
            </div>
          </div>
        ))}
        {active && (
          <p className="onboarding-chat-prompt onboarding-chat-question" ref={promptRef} tabIndex={-1}>
            <span className="sr-only">{assistant}: </span>{active === 'history' ? historyQuestion(historyYear) : questions[active]}
          </p>
        )}
      </div>

      {active && (
        <div className="onboarding-chat-reply">
          <div className="onboarding-chat-options" role="group" aria-label={labels[active]}>
            {active === 'history' && (
              <>
                {onboardingCrops.map((item) => (
                  <button className="button subtle" type="button" key={item} disabled={busy}
                    onClick={() => setHistory(item)}>{cropLabel(item)}</button>
                ))}
                <button className="button subtle" type="button" disabled={busy}
                  onClick={() => setHistory(null)}>{uk ? 'Не пам’ятаю' : 'I don’t remember'}</button>
              </>
            )}
            {active === 'allowed_crops' && onboardingCrops.map((item) => (
              <button type="button" key={item} className={`button ${currentSelection.includes(item) ? 'primary' : 'subtle'}`}
                aria-pressed={currentSelection.includes(item)} disabled={busy} onClick={() => toggleCrop(item)}>
                {currentSelection.includes(item) && <CheckIcon aria-hidden="true" />}{cropLabel(item)}
              </button>
            ))}
            {active === 'irrigation' && [true, false, null].map((value) => (
              <button className="button subtle" type="button" key={String(value)} disabled={busy}
                onClick={() => applyAnswer({ irrigation: value })}>{irrigationLabel(value)}</button>
            ))}
            {active === 'priority' && priorities.map((value) => (
              <button className="button subtle" type="button" key={value} disabled={busy}
                onClick={() => applyAnswer({ priority: value })}>{goals[value]}</button>
            ))}
          </div>
          <form className="onboarding-chat-composer" onSubmit={send}>
            <input value={text} disabled={busy} onChange={(event) => { setText(event.target.value); setFeedback(undefined) }}
              placeholder={placeholder} aria-label={uk ? 'Ваша відповідь' : 'Your answer'} maxLength={180} />
            <button type="submit" className="button primary" disabled={busy || !text.trim()}
              aria-label={uk ? 'Надіслати відповідь' : 'Send answer'}><ArrowUpIcon aria-hidden="true" /></button>
          </form>
          {feedback && <p className={feedback.error ? 'onboarding-chat-invalid' : 'muted'} role="status">{feedback.message}</p>}
        </div>
      )}

      {!active && (
        <p className="onboarding-chat-prompt" ref={promptRef} tabIndex={-1}>
          {uk ? 'Перевірте відповіді вище. Коли все правильно, можна переглянути демонстраційні стратегії.' : 'Review your answers above. When they are correct, view the demo rotation strategies.'}
        </p>
      )}
      {error && <ErrorBox message={error} />}
      <footer className="onboarding-chat-footer">
        <button className="button subtle" type="button" disabled={busy} onClick={onBack}>
          <ArrowLeftIcon aria-hidden="true" />{uk ? 'До межі поля' : 'Back to field boundary'}
        </button>
        {editing && (
          <button className="button subtle" type="button" disabled={busy}
            onClick={() => { setEditing(undefined); setHistoryYearOverride(undefined); setText(''); setFeedback(undefined); setSelected(answers.allowed_crops) }}>
            {uk ? 'Скасувати зміну' : 'Cancel edit'}
          </button>
        )}
        {active === 'allowed_crops' && (
          <button className="button primary" type="button" disabled={busy || currentSelection.length < 1}
            onClick={() => applyAnswer({ allowed_crops: currentSelection })}>
            {uk ? 'Підтвердити культури' : 'Confirm crops'}<ArrowRightIcon aria-hidden="true" />
          </button>
        )}
        {!active && (
          <button className="button primary" type="button" disabled={busy} onClick={onComplete}>
            {busy ? (uk ? 'Зберігаємо…' : 'Saving…') : (uk ? 'Переглянути стратегії сівозміни' : 'View rotation strategies')}
            <ArrowRightIcon aria-hidden="true" />
          </button>
        )}
      </footer>
    </section>
  )
}
