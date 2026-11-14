import type { CSSProperties } from 'react'
import { FieldMark } from '@field-shift/agent-widget'
import './assistantMark.css'

/** «Польовий помічник»'s mark (its launcher look). Decorative: the surrounding control owns the label. */
export function AssistantMark({ size = 40, className = '' }: { size?: number; className?: string }) {
  return (
    <span className={`assistant-mark ${className}`} aria-hidden="true"
      style={{ '--assistant-mark-size': `${size}px` } as CSSProperties}>
      <FieldMark />
    </span>
  )
}
