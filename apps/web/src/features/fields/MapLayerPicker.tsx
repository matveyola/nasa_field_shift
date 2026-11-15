import { useLocale } from '../../shared/locale'
import { useEffect, useId, useRef } from 'react'
import { CheckIcon, ChevronDownIcon, PhotoIcon, Square3Stack3DIcon } from '@heroicons/react/24/outline'
import { basemaps, type BasemapId } from './basemaps'
import type { VisualizationId } from './fieldMapVisualization'

type Props = {
  basemap: BasemapId
  layers: ReadonlyArray<{ id: VisualizationId; label: string }>
  activeVisualization: VisualizationId
  showBoundaries: boolean
  fieldAvailable: boolean
  onBasemap: (id: BasemapId) => void
  onVisualization: (id: VisualizationId) => void
  onBoundaries: (shown: boolean) => void
}

const basemapIds: BasemapId[] = ['aerial', 'street']

export function MapLayerPicker({
  basemap, layers, activeVisualization, showBoundaries, fieldAvailable,
  onBasemap, onVisualization, onBoundaries,
}: Props) {
  const { language, t } = useLocale()
  const radioGroup = useId()
  const picker = useRef<HTMLDetailsElement>(null)
  const trigger = useRef<HTMLElement>(null)
  const noVisualization = language === 'uk' ? 'Без візуалізації' : 'No visualization'

  useEffect(() => {
    function closeOnOutsidePointer(event: PointerEvent) {
      const details = picker.current
      if (details?.open && event.target instanceof Node && !details.contains(event.target)) {
        details.open = false
      }
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer, true)
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer, true)
  }, [])

  function close() {
    if (picker.current) picker.current.open = false
    trigger.current?.focus()
  }

  return (
    <details className="map-layer-picker" ref={picker} onKeyDown={(event) => {
      if (event.key === 'Escape' && picker.current?.open) {
        event.preventDefault()
        close()
      }
    }}>
      <summary className="map-layer-trigger" ref={trigger}>
        <Square3Stack3DIcon aria-hidden="true" /> {t('Layers')} <ChevronDownIcon aria-hidden="true" />
      </summary>
      <div className="map-layer-panel">
        <div role="group" aria-label={t('Basemap')}>
          <p className="map-layer-heading" aria-hidden="true">{t('Map background')}</p>
          <div className="map-basemap-options">
            {basemapIds.map((id) => (
              <button key={id} type="button" className="map-basemap-option"
                aria-pressed={id === basemap}
                onClick={() => { onBasemap(id); close() }}>
                <span className={'map-basemap-preview map-preview-' + id} aria-hidden="true">
                  {id === basemap && <CheckIcon className="map-basemap-check" />}
                </span>
                {t(basemaps[id].label)}
              </button>
            ))}
          </div>
        </div>
        <div className="map-overlay-options" role="radiogroup" aria-label={t('Map overlay')}>
          <p className="map-layer-heading" aria-hidden="true">{t('Field data')}</p>
          {[{ id: 'none' as const, label: noVisualization }, ...layers].map(({ id, label }) => (
            <label key={id} className={'map-layer-option' + (id === 'satellite' ? ' map-satellite-option' : '')}>
              <input type="radio" name={radioGroup} value={id} checked={activeVisualization === id}
                disabled={id !== 'none' && !fieldAvailable} onChange={() => onVisualization(id)} />
              <span>{label}</span>
              {id === 'satellite' ? <PhotoIcon aria-hidden="true" /> : id !== 'none' && id !== 'forecast' &&
                <span className={'map-layer-swatch map-swatch-' + id} aria-hidden="true" />}
            </label>
          ))}
        </div>
        <label className="map-layer-option">
          <input type="checkbox" checked={showBoundaries} onChange={(event) => onBoundaries(event.target.checked)} />
          <span>{t('Field boundaries')}</span>
          <span className="map-layer-swatch map-swatch-boundaries" aria-hidden="true" />
        </label>
      </div>
    </details>
  )
}
