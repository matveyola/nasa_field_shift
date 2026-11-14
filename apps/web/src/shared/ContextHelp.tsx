import { InformationCircleIcon } from '@heroicons/react/24/outline'
import { useEffect, useId, useLayoutEffect, useRef, useState, type FocusEvent, type PointerEvent, type ReactNode, type SyntheticEvent } from 'react'
import { contextHelpPlacement } from './contextHelpPlacement'
import { useLocale } from './locale'
import './contextHelp.css'

function stopPropagation(event: SyntheticEvent) {
  event.stopPropagation()
}

/** Mount beside the control being explained, as a sibling of its label or button. */
export function ContextHelp({ label, children }: { label: string; children: ReactNode }) {
  const { language } = useLocale()
  const id = useId()
  const panelId = id + '-panel'
  const dialogId = id + '-dialog'
  const descriptionId = id + '-description'
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const activated = useRef(false)
  const restoringFocus = useRef(false)
  const pointerDismissal = useRef<number>(undefined)
  const [open, setOpen] = useState(false)

  useEffect(() => () => window.clearTimeout(pointerDismissal.current), [])

  useLayoutEffect(() => {
    const button = trigger.current
    const popover = panel.current
    const body = content.current
    if (!open || !button || !popover || !body) return

    const visual = window.visualViewport
    function place() {
      if (!button || !popover || !body || !popover.matches(':popover-open')) return
      const viewport = {
        left: visual?.offsetLeft ?? 0,
        top: visual?.offsetTop ?? 0,
        width: visual?.width ?? document.documentElement.clientWidth,
        height: visual?.height ?? document.documentElement.clientHeight,
      }
      const anchor = button.getBoundingClientRect()
      if (anchor.height === 0 || anchor.bottom <= viewport.top || anchor.top >= viewport.top + viewport.height) {
        popover.hidePopover()
        return
      }
      popover.style.maxWidth = `${viewport.width - 16}px`
      const placement = contextHelpPlacement(anchor, viewport, { width: popover.offsetWidth, contentHeight: body.scrollHeight })
      popover.dataset.side = placement.side
      popover.style.left = `${placement.left}px`
      popover.style.top = `${placement.top}px`
      body.style.maxHeight = `${placement.contentHeight}px`
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape' || !popover?.matches(':popover-open')) return
      // Reserve this Escape for the help panel when it sits inside another dismissible control.
      event.preventDefault()
      event.stopPropagation()
      close()
    }
    const resizeObserver = new ResizeObserver(place)
    resizeObserver.observe(button)
    resizeObserver.observe(body)
    document.addEventListener('keydown', onEscape, true)
    document.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    visual?.addEventListener('resize', place)
    visual?.addEventListener('scroll', place)
    place()
    return () => {
      resizeObserver.disconnect()
      document.removeEventListener('keydown', onEscape, true)
      document.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
      visual?.removeEventListener('resize', place)
      visual?.removeEventListener('scroll', place)
    }
  }, [open])

  function show() {
    window.clearTimeout(pointerDismissal.current)
    if (!panel.current?.matches(':popover-open')) {
      panel.current?.showPopover()
      setOpen(true)
    }
  }

  function close() {
    window.clearTimeout(pointerDismissal.current)
    if (!panel.current?.matches(':popover-open')) return
    const restoreFocus = panel.current.contains(document.activeElement)
    activated.current = false
    restoringFocus.current = true
    panel.current.hidePopover()
    if (restoreFocus) trigger.current?.focus({ preventScroll: true })
    restoringFocus.current = false
    setOpen(false)
  }

  function contains(target: EventTarget | null) {
    return target instanceof Node && (trigger.current?.contains(target) || panel.current?.contains(target))
  }

  function leavePointer(event: PointerEvent) {
    if (event.pointerType !== 'mouse' || activated.current || contains(event.relatedTarget)) return
    window.clearTimeout(pointerDismissal.current)
    // Give the pointer time to reach a panel that is shifted away from the edge of the screen.
    pointerDismissal.current = window.setTimeout(() => {
      if (activated.current || trigger.current?.matches(':focus-visible, :hover') || panel.current?.matches(':hover')
        || panel.current?.contains(document.activeElement)) return
      close()
    }, 250)
  }

  function leaveFocus(event: FocusEvent) {
    if (contains(event.relatedTarget)) return
    if (!activated.current && (trigger.current?.matches(':hover') || panel.current?.matches(':hover'))) return
    close()
  }

  const isolatedPointerEvents = {
    onPointerDown: stopPropagation,
    onMouseDown: stopPropagation,
    onTouchStart: stopPropagation,
    onDoubleClick: stopPropagation,
  }
  return (
    <>
      <button ref={trigger} id={id} type="button" className="context-help-trigger"
        aria-label={`${language === 'uk' ? 'Пояснення' : 'Explanation'}: ${label}`}
        aria-haspopup="dialog" aria-controls={dialogId} aria-expanded={open} popoverTarget={panelId}
        aria-describedby={open ? descriptionId : undefined}
        {...isolatedPointerEvents}
        onPointerEnter={(event) => { if (event.pointerType === 'mouse') show() }}
        onPointerLeave={leavePointer}
        onFocus={(event) => {
          if (!restoringFocus.current && event.currentTarget.matches(':focus-visible')) show()
        }}
        onBlur={leaveFocus}
        onClick={(event) => {
          event.stopPropagation()
          event.preventDefault()
          if (activated.current && panel.current?.matches(':popover-open')) close()
          else {
            show()
            activated.current = true
            content.current?.focus({ preventScroll: true })
          }
        }}>
        <InformationCircleIcon aria-hidden="true" />
      </button>
      <div ref={panel} id={panelId} className="context-help-panel" popover="auto"
        {...isolatedPointerEvents} onClick={stopPropagation}
        onPointerEnter={() => window.clearTimeout(pointerDismissal.current)}
        onPointerLeave={leavePointer} onBlur={leaveFocus}
        onToggle={(event) => {
          const isOpen = event.currentTarget.matches(':popover-open')
          if (!isOpen) {
            activated.current = false
            window.clearTimeout(pointerDismissal.current)
          }
          setOpen(isOpen)
        }}>
        <div ref={content} id={dialogId} className="context-help-content" role="dialog" tabIndex={-1}
          aria-labelledby={id} aria-describedby={descriptionId}>
          <div id={descriptionId} className="context-help-description">{children}</div>
        </div>
      </div>
    </>
  )
}
