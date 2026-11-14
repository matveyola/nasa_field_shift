import { useEffect, useId, useRef, useState } from 'react'
import { useLocale } from '../../shared/locale'

const storageKey = 'field-shift-field-panel-width'
const widthProperty = '--field-panel-width'
const minWidth = 275
const maxWidth = 600
const mapMinimum = 320
const dividerWidth = 8

function readWidth(): number | undefined {
  try {
    const saved = localStorage.getItem(storageKey)
    if (!saved?.trim()) return undefined
    const value = Number(saved)
    return Number.isFinite(value) && value >= minWidth && value <= maxWidth ? value : undefined
  } catch { return undefined }
}

function saveWidth(width?: number) {
  try {
    if (width === undefined) localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, String(width))
  } catch { /* Resizing still works when browser storage is unavailable. */ }
}

type Drag = { pointerId: number; startX: number; startWidth: number; latestWidth: number }

export function FieldPanelResizer() {
  const { t, number } = useLocale()
  const divider = useRef<HTMLDivElement>(null)
  const helpId = useId()
  const [size, setSize] = useState({ current: 365, min: minWidth, max: maxWidth })

  useEffect(() => {
    const handle = divider.current
    const layout = handle?.parentElement
    const panel = layout?.querySelector<HTMLElement>('.field-context-panel')
    if (!handle || !layout || !panel) return
    const desktop = matchMedia('(min-width: 1001px)')
    let preferred = readWidth()
    let drag: Drag | undefined
    let frame: number | undefined
    let lastContainerWidth = layout.clientWidth

    function bounds() {
      const min = window.innerWidth <= 1060 ? minWidth : 305
      return { min, max: Math.max(min, Math.min(maxWidth, layout!.clientWidth - mapMinimum - dividerWidth)) }
    }

    function clamp(value: number) {
      const { min, max } = bounds()
      return Math.round(Math.max(min, Math.min(max, value)))
    }

    function report() {
      const next = { ...bounds(), current: Math.round(panel!.getBoundingClientRect().width) }
      setSize((current) => current.current === next.current && current.min === next.min && current.max === next.max ? current : next)
    }

    function apply(width: number) {
      layout!.style.setProperty(widthProperty, clamp(width) + 'px')
      report()
    }

    function sync() {
      if (!desktop.matches || preferred === undefined) layout!.style.removeProperty(widthProperty)
      else apply(preferred)
      if (desktop.matches) report()
    }

    function finish(commit: boolean) {
      const active = drag
      if (!active) return
      drag = undefined
      if (frame !== undefined) cancelAnimationFrame(frame)
      frame = undefined
      layout!.classList.remove('is-resizing-field-panel')
      if (commit && Math.abs(active.latestWidth - active.startWidth) >= 1) {
        preferred = clamp(active.latestWidth)
        saveWidth(preferred)
      }
      sync()
      if (handle!.hasPointerCapture(active.pointerId)) handle!.releasePointerCapture(active.pointerId)
    }

    function pointerDown(event: PointerEvent) {
      if (!desktop.matches || !event.isPrimary || event.button !== 0 || drag) return
      event.preventDefault()
      handle!.focus({ preventScroll: true })
      const width = panel!.getBoundingClientRect().width
      try { handle!.setPointerCapture(event.pointerId) } catch { return }
      drag = { pointerId: event.pointerId, startX: event.clientX, startWidth: width, latestWidth: width }
      layout!.classList.add('is-resizing-field-panel')
    }

    function pointerMove(event: PointerEvent) {
      if (!drag || event.pointerId !== drag.pointerId) return
      drag.latestWidth = clamp(drag.startWidth + event.clientX - drag.startX)
      if (frame === undefined) frame = requestAnimationFrame(() => {
        frame = undefined
        if (drag) apply(drag.latestWidth)
      })
    }

    function pointerUp(event: PointerEvent) {
      if (!drag || event.pointerId !== drag.pointerId) return
      drag.latestWidth = clamp(drag.startWidth + event.clientX - drag.startX)
      finish(true)
    }

    function cancelPointer(event: PointerEvent) {
      if (drag?.pointerId === event.pointerId) finish(false)
    }

    function reset() {
      if (!desktop.matches) return
      finish(false)
      preferred = undefined
      saveWidth()
      sync()
    }

    function keyDown(event: KeyboardEvent) {
      if (!desktop.matches) return
      if (event.key === 'Escape' && drag) { event.preventDefault(); finish(false); return }
      if (drag) return
      if (event.key === 'Enter') { event.preventDefault(); reset(); return }
      const { min, max } = bounds()
      const current = panel!.getBoundingClientRect().width
      const next = event.key === 'ArrowLeft' ? current - 16 : event.key === 'ArrowRight' ? current + 16
        : event.key === 'Home' ? min : event.key === 'End' ? max : undefined
      if (next === undefined) return
      event.preventDefault()
      preferred = clamp(next)
      saveWidth(preferred)
      apply(preferred)
    }

    function viewportChange() {
      finish(false)
      sync()
      if (!desktop.matches && document.activeElement === handle) panel!.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true })
    }

    function blur() { finish(false) }

    const observer = new ResizeObserver(() => {
      if (layout.clientWidth === lastContainerWidth) return
      lastContainerWidth = layout.clientWidth
      viewportChange()
    })
    observer.observe(layout)
    desktop.addEventListener('change', viewportChange)
    window.addEventListener('blur', blur)
    handle.addEventListener('pointerdown', pointerDown)
    handle.addEventListener('pointermove', pointerMove)
    handle.addEventListener('pointerup', pointerUp)
    handle.addEventListener('pointercancel', cancelPointer)
    handle.addEventListener('lostpointercapture', cancelPointer)
    handle.addEventListener('dblclick', reset)
    handle.addEventListener('keydown', keyDown)
    sync()

    return () => {
      observer.disconnect()
      desktop.removeEventListener('change', viewportChange)
      window.removeEventListener('blur', blur)
      handle.removeEventListener('pointerdown', pointerDown)
      handle.removeEventListener('pointermove', pointerMove)
      handle.removeEventListener('pointerup', pointerUp)
      handle.removeEventListener('pointercancel', cancelPointer)
      handle.removeEventListener('lostpointercapture', cancelPointer)
      handle.removeEventListener('dblclick', reset)
      handle.removeEventListener('keydown', keyDown)
      if (frame !== undefined) cancelAnimationFrame(frame)
      if (drag && handle.hasPointerCapture(drag.pointerId)) handle.releasePointerCapture(drag.pointerId)
      layout.classList.remove('is-resizing-field-panel')
      layout.style.removeProperty(widthProperty)
    }
  }, [])

  return <div ref={divider} className="field-panel-resizer" role="separator" tabIndex={0}
    aria-label={t('Selected field')} aria-orientation="vertical" aria-controls="field-context-panel"
    aria-valuemin={size.min} aria-valuemax={size.max} aria-valuenow={size.current} aria-valuetext={number(size.current, 0) + ' px'}
    aria-describedby={helpId} title={t('Drag to resize. Double-click to reset.')}>
    <span className="field-panel-resizer-grip" aria-hidden="true" />
    <span id={helpId} className="sr-only">{t('Use Left and Right arrows to resize, Home and End for the limits, and Enter to reset.')}</span>
  </div>
}
