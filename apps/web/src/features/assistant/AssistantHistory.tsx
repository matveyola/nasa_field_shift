import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../../api/client'
import type { Conversation, ConversationDetail } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import './assistant-history.css'

const copy = {
  en: {
    title: 'Saved conversations', close: 'Close saved conversations', loading: 'Loading…', retry: 'Try again',
    empty: 'No saved conversations for this field.', back: 'All saved conversations',
    note: 'Read-only records of earlier conversations. They do not describe the field’s current condition.',
    persistence: 'Only conversations saved by the service appear here. New chats in the current assistant are not saved to this list.',
    limit: 'Up to 50 recent conversations; up to 100 recent messages in each.',
    year: 'Harvest year', scenario: 'Scenario', updated: 'Updated', you: 'You', assistant: 'Assistant',
    demo: 'Example response', sources: 'Recorded sources', error: 'Saved conversations could not be loaded.',
    mismatch: 'This saved conversation does not match the selected field or record.',
  },
  uk: {
    title: 'Збережені розмови', close: 'Закрити збережені розмови', loading: 'Завантаження…', retry: 'Спробувати ще раз',
    empty: 'Для цього поля немає збережених розмов.', back: 'Усі збережені розмови',
    note: 'Попередні розмови лише для перегляду. Вони не описують поточний стан поля.',
    persistence: 'Тут лише розмови, збережені сервісом. Нові чати в поточному помічнику до цього списку не зберігаються.',
    limit: 'До 50 останніх розмов; до 100 останніх повідомлень у кожній.',
    year: 'Рік урожаю', scenario: 'Сценарій', updated: 'Оновлено', you: 'Ви', assistant: 'Помічник',
    demo: 'Приклад відповіді', sources: 'Збережені джерела', error: 'Не вдалося завантажити збережені розмови.',
    mismatch: 'Збережена розмова не відповідає вибраному полю або запису.',
  },
} as const

function sourceLink(value: string): string | undefined {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : undefined
  } catch { return undefined }
}

/** No stored answer, citation, option or action is fed back into the live assistant. */
export function AssistantHistory({ fieldId, fieldName, onClose }: {
  fieldId: string; fieldName: string; onClose: () => void
}) {
  const { language, t } = useLocale()
  const c = copy[language]
  const dialog = useRef<HTMLDialogElement>(null)
  const heading = useId()
  const note = useId()
  const [rows, setRows] = useState<Conversation[]>()
  const [chosen, setChosen] = useState<Conversation>()
  const [detail, setDetail] = useState<ConversationDetail>()
  const [problem, setProblem] = useState<'error' | 'mismatch'>()
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const element = dialog.current!
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    // A native top-layer dialog makes the widget inert without unmounting its draft/thread.
    // Its key events belong to this host sibling, never to the widget's own mobile focus trap.
    element.showModal()
    return () => {
      element.close()
      if (trigger?.isConnected && trigger.getClientRects().length) trigger.focus()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setRows(undefined)
    setChosen(undefined)
    setDetail(undefined)
    setProblem(undefined)
    api.conversations(fieldId, controller.signal).then(value => {
      if (controller.signal.aborted) return
      if (!Array.isArray(value) || value.some(row => row.field_id !== fieldId)) setProblem('mismatch')
      else setRows(value)
    }).catch(() => { if (!controller.signal.aborted) setProblem('error') })
    return () => controller.abort()
  }, [fieldId, attempt])

  useEffect(() => {
    setDetail(undefined)
    if (!chosen) return
    const controller = new AbortController()
    setProblem(undefined)
    api.conversation(chosen.id, controller.signal).then(value => {
      if (controller.signal.aborted) return
      if (value.field_id !== fieldId || value.id !== chosen.id || value.season !== chosen.season || value.scenario !== chosen.scenario) {
        setProblem('mismatch')
      } else setDetail(value)
    }).catch(() => { if (!controller.signal.aborted) setProblem('error') })
    return () => controller.abort()
  }, [chosen, fieldId])

  useEffect(() => {
    // Replacing the list removes its focused button. Keep focus in the modal as content changes.
    dialog.current?.querySelector<HTMLButtonElement>(chosen ? '[data-history-back]' : '[data-history-close]')?.focus()
  }, [chosen])

  const currentDetail = chosen && detail?.id === chosen.id && detail.field_id === fieldId ? detail : undefined

  function date(value: string) {
    const parsed = new Date(value)
    return Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat(language === 'uk' ? 'uk-UA' : 'en', {
      dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
    }).format(parsed) + ' UTC' : '—'
  }

  return createPortal(<dialog ref={dialog} className="assistant-history" aria-labelledby={heading} aria-describedby={note}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose() }}
    onKeyDown={event => {
      if (event.key !== 'Tab') return
      event.stopPropagation()
      const items = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]')]
        .filter(element => element.getClientRects().length > 0)
      const first = items[0]
      const last = items.at(-1)
      // Native modal isolation prevents background focus; wrap here to avoid browser-chrome focus.
      if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus() }
    }}>
    <header className="assistant-history-heading">
      <div><h2 id={heading}>{c.title}</h2><p>{fieldName}</p></div>
      <button type="button" className="button subtle" data-history-close autoFocus onClick={onClose} aria-label={c.close}>×</button>
    </header>
    <div className="assistant-history-body">
      <p id={note}>{c.note}</p>
      <p className="assistant-history-help">{c.persistence} {c.limit}</p>
      {chosen && <button type="button" data-history-back className="button subtle" onClick={() => { setChosen(undefined); setProblem(undefined) }}>{c.back}</button>}
      {problem ? <div role="alert"><p>{c[problem]}</p><button type="button" className="button" onClick={() => setAttempt(value => value + 1)}>{c.retry}</button></div>
        : !rows || (chosen && !currentDetail) ? <p role="status">{c.loading}</p>
        : currentDetail && chosen ? <section aria-label={currentDetail.title}>
          <h3>{currentDetail.title}</h3>
          <p>{c.year}: {currentDetail.season} · {c.scenario}: {t(currentDetail.scenario)} · {c.updated}: {date(currentDetail.updated_at)}</p>
          <ol className="assistant-history-messages">
            {currentDetail.messages.map(message => <li key={message.id} className={'assistant-history-message assistant-history-message--' + message.role}>
              <div className="assistant-history-message-meta"><strong>{message.role === 'user' ? c.you : c.assistant}</strong>
                <time dateTime={message.created_at}>{date(message.created_at)}</time>
                {message.role === 'assistant' && message.demo && <span className="chip demo-chip">{c.demo}</span>}
              </div>
              <p className="assistant-history-text">{message.text}</p>
              {!!message.citations?.length && <div><strong>{c.sources}</strong><ul>
                {message.citations.map((citation, index) => {
                  const href = sourceLink(citation.url)
                  return <li key={citation.id + ':' + index}>{href
                    ? <a href={href} target="_blank" rel="noopener noreferrer">{citation.title}</a>
                    : <span>{citation.title}</span>}{citation.publisher ? ` · ${citation.publisher}` : ''}{citation.year ? ` · ${citation.year}` : ''}</li>
                })}
              </ul></div>}
            </li>)}
          </ol>
        </section> : rows.length === 0 ? <p>{c.empty}</p> : <ul className="assistant-history-list">
          {rows.map(row => <li key={row.id}><button type="button" onClick={() => setChosen(row)}>
            <strong>{row.title}</strong><span>{c.year}: {row.season} · {c.scenario}: {t(row.scenario)}</span>
            <span>{c.updated}: {date(row.updated_at)}</span>
          </button></li>)}
        </ul>}
    </div>
  </dialog>, document.body)
}
