import type { ReactNode } from 'react'
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/24/outline'
import type { Field } from '../api/contracts'
import { CropIcon } from './CropIcon'
import { useLocale } from './locale'
import './fieldWorkspaceBar.css'

type Props = {
  field: Field
  fields: Field[]
  chooseField: (fieldId: string) => void
  openFields?: () => void
  disabled?: boolean
  children?: ReactNode
}

export function FieldWorkspaceBar({ field, fields, chooseField, openFields, disabled = false, children }: Props) {
  const { t, number, crop } = useLocale()
  const index = fields.findIndex(({ id }) => id === field.id)
  const canSwitch = !disabled && fields.length > 1 && index >= 0

  function step(direction: -1 | 1) {
    if (!canSwitch) return
    chooseField(fields[(index + direction + fields.length) % fields.length].id)
    // Field-specific pages remount to discard the previous field's drafts and requests.
    requestAnimationFrame(() => {
      if (document.activeElement === document.body) {
        document.getElementById(direction === 1 ? 'field-workspace-next' : 'field-workspace-previous')?.focus({ preventScroll: true })
      }
    })
  }

  return <div className="field-workspace-bar">
    {openFields && <button className="button field-workspace-back" type="button" disabled={disabled} onClick={openFields}>
      <ChevronLeftIcon aria-hidden="true" />{t('Back to map')}
    </button>}
    <div className="field-workspace-switcher" role="group" aria-label={t('Selected field')}>
      <button id="field-workspace-previous" className="icon-button" type="button" aria-label={t('Previous field')} disabled={!canSwitch} onClick={() => step(-1)}>
        <ChevronLeftIcon aria-hidden="true" />
      </button>
      <strong className="field-workspace-name" title={field.name} aria-live="polite">{field.name}</strong>
      <button id="field-workspace-next" className="icon-button field-workspace-next" type="button" aria-label={t('Next field')} disabled={!canSwitch} onClick={() => step(1)}>
        <ChevronRightIcon aria-hidden="true" />
      </button>
      <span className="crop-label field-workspace-crop"><CropIcon crop={field.crop} /><span>{crop(field.crop)} · {number(field.area_ha)} {t('ha')}</span></span>
    </div>
    {children && <div className="field-workspace-actions">{children}</div>}
  </div>
}
