import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ArrowPathIcon, ArrowRightIcon, BellIcon, CheckIcon, XMarkIcon } from '@heroicons/react/24/outline'
import type { Field } from '../../api/contracts'
import { AssistantMark } from '../../shared/AssistantMark'
import { HeaderActionsContext } from '../../shared/HeaderActions'
import { useLocale } from '../../shared/locale'
import type { FieldNotification } from './fieldNotifications'
import { markNotificationsRead, parseReadNotifications } from './notificationReadState'
import { useFieldNotifications } from './useFieldNotifications'
import './notifications.css'

type NotificationContext = { count: number; open: (trigger: HTMLButtonElement) => void; expanded: boolean; dialogId: string }
const Notifications = createContext<NotificationContext | null>(null)

export function NotificationButton({ className = '' }: { className?: string }) {
  const context = useContext(Notifications)
  const { t } = useLocale()
  if (!context) return null
  return <button type="button" className={`notification-trigger ${className}`} aria-haspopup="dialog"
    aria-controls={context.dialogId} aria-expanded={context.expanded}
    aria-label={`${t('Notifications')}${context.count ? ` · ${context.count} ${t('unread')}` : ''}`}
    onClick={(event) => context.open(event.currentTarget)}>
    <BellIcon aria-hidden="true" />
    {context.count > 0 && <span className="notification-count" aria-hidden="true">{context.count > 99 ? '99+' : context.count}</span>}
  </button>
}

export function NotificationProvider({ fields, season, farmKey, children, openAgent, openAnalysis }: {
  fields: Field[]; season: number; farmKey: string; children: ReactNode
  openAgent: (item: FieldNotification) => void
  openAnalysis: (item: FieldNotification) => void
}) {
  const { t, shortDate, number } = useLocale()
  const resource = useFieldNotifications(fields, season)
  const storageKey = `field-shift-notifications:v1:${encodeURIComponent(farmKey)}:${season}`
  const [read, setRead] = useState<{ key: string; ids: string[] }>(() => {
    try { return { key: storageKey, ids: parseReadNotifications(localStorage.getItem(storageKey)) } }
    catch { return { key: storageKey, ids: [] } }
  })
  useEffect(() => {
    const sync = () => {
      try { setRead({ key: storageKey, ids: parseReadNotifications(localStorage.getItem(storageKey)) }) }
      catch { setRead({ key: storageKey, ids: [] }) }
    }
    sync()
    const onStorage = (event: StorageEvent) => { if (event.key === storageKey || event.key === null) sync() }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [storageKey])
  const readIds = new Set(read.key === storageKey ? read.ids : [])
  const items = resource.data?.items ?? []
  const unread = items.filter((item) => !readIds.has(item.id)).length
  const [expanded, setExpanded] = useState(false)
  const dialogId = useId()
  const headingId = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)

  function open(button: HTMLButtonElement) {
    trigger.current = button
    dialog.current?.showModal()
    setExpanded(true)
    closeButton.current?.focus({ preventScroll: true })
  }
  function close() { dialog.current?.close() }
  function markRead(ids: string[]) {
    let previous = read.key === storageKey ? read.ids : []
    try { previous = markNotificationsRead(previous, parseReadNotifications(localStorage.getItem(storageKey))) } catch { /* Session state remains usable. */ }
    const next = markNotificationsRead(previous, ids)
    setRead({ key: storageKey, ids: next })
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Session state remains usable. */ }
  }
  function follow(item: FieldNotification, destination: 'agent' | 'analysis') {
    // A background refresh may remove an obsolete signal while its button has focus.
    if (!items.some((current) => current.id === item.id)) return
    markRead([item.id])
    close()
    if (destination === 'agent') openAgent(item)
    else openAnalysis(item)
  }

  return <Notifications value={{ count: unread, open, expanded, dialogId }}>
    <HeaderActionsContext value={<NotificationButton />}>{children}</HeaderActionsContext>
    <dialog id={dialogId} ref={dialog} className="notification-dialog" aria-labelledby={headingId}
      onClose={() => { setExpanded(false); trigger.current?.focus({ preventScroll: true }) }}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
          .filter((button) => button.getClientRects().length > 0)
        const first = buttons[0], last = buttons.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }}
      onClick={(event) => { if (event.target === event.currentTarget) close() }}>
      <div className="notification-surface">
        <header className="notification-heading">
          <div><h2 id={headingId}>{t('Notifications')}</h2><p>{t('Signals from your fields')}</p></div>
          {unread > 0 && <span className="notification-new">{unread} {t('unread')}</span>}
          <button ref={closeButton} className="icon-button" type="button" onClick={close} aria-label={t('Close notifications')}><XMarkIcon aria-hidden="true" /></button>
        </header>
        <div className="notification-tools">
          <button type="button" disabled={resource.pending} onClick={resource.reload}><ArrowPathIcon aria-hidden="true" />{t('Refresh signals')}</button>
          {unread > 0 && <button type="button" onClick={() => markRead(items.map(({ id }) => id))}><CheckIcon aria-hidden="true" />{t('Mark all as read')}</button>}
        </div>
        <div className="notification-body" aria-busy={resource.pending}>
          {resource.pending && !resource.data && <p className="notification-state" role="status">{t('Checking saved field data…')}</p>}
          {resource.failed && <p className="notification-state" role="alert">{t('Could not check field signals. Try again.')}</p>}
          {resource.data && !items.length && <div className="notification-empty">
            <AssistantMark size={56} /><h3>{t('No current alerts')}</h3>
            <p>{t('New signals will appear here when saved field data indicates something to inspect.')}</p>
          </div>}
          <ul className="notification-list">
            {items.map((item) => <li key={item.id} className={readIds.has(item.id) ? 'is-read' : 'is-unread'}>
              <div className="notification-item-heading"><AssistantMark size={36} />
                <div><strong>{item.fieldName}</strong><h3>{t(item.kind === 'water' ? 'Possible water stress' : 'Inspect the lower-NDVI zones')}</h3></div>
                {!readIds.has(item.id) && <span className="notification-unread-dot" aria-label={t('Unread')} />}
              </div>
              <p className="notification-detail">{item.kind === 'water'
                ? <>{t('Below the stress threshold from')} {shortDate(item.date)}<br />{t('Minimum reserve')} {number(item.minimumMm)} {t('mm')} · {t('Stress threshold')} {number(item.thresholdMm)} {t('mm')}</>
                : <>{number(item.lowAreaPercent)}% {t('of observed area has lower NDVI')} · {shortDate(item.date)}</>}</p>
              <p className="notification-source">{item.source} · {t(item.kind === 'water' ? 'Modelled forecast' : 'Satellite inspection cue')}</p>
              <button type="button" className="button primary notification-chat-link" onClick={() => follow(item, 'agent')}>{t('Review with assistant')}<ArrowRightIcon aria-hidden="true" /></button>
              <button type="button" className="notification-analysis-link" onClick={() => follow(item, 'analysis')}>{t(item.kind === 'water' ? 'Open water forecast' : 'Open vegetation analysis')}<ArrowRightIcon aria-hidden="true" /></button>
            </li>)}
          </ul>
          {Boolean(resource.data?.unavailableFields) && <p className="notification-coverage" role="status">{t('Some field data is missing or outdated. No alert does not mean no risk.')} ({resource.data?.unavailableFields}/{fields.length})</p>}
        </div>
        <footer className="notification-footer">{t('Updated from saved forecasts and Sentinel-2 observations. Read status is saved on this device.')}</footer>
      </div>
    </dialog>
  </Notifications>
}
