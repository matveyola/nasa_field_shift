import type { ReactNode } from 'react'
import { nullableNumber } from './model'

export function NumberInput({ label, value, onChange, min = 0, max, required = false, disabled = false }: {
  label: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number; required?: boolean; disabled?: boolean
}) {
  return <label className="pl-control"><span>{label}</span><input type="number" step="any" min={min} max={max} required={required} disabled={disabled}
    value={value !== null && Number.isFinite(value) ? value : ''} onChange={event => onChange(nullableNumber(event.target.value))} /></label>
}
export function TextInput({ label, value, onChange, type = 'text', required = false }: {
  label: string; value: string | null; onChange: (value: string | null) => void; type?: 'text' | 'date'; required?: boolean
}) {
  return <label className="pl-control"><span>{label}</span><input type={type} value={value ?? ''} required={required}
    onChange={event => onChange(event.target.value || null)} /></label>
}
export function Select({ label, value, onChange, children, disabled = false }: {
  label: string; value: string; onChange: (value: string) => void; children: ReactNode; disabled?: boolean
}) {
  return <label className="pl-control"><span>{label}</span><select disabled={disabled} value={value} onChange={event => onChange(event.target.value)}>{children}</select></label>
}
