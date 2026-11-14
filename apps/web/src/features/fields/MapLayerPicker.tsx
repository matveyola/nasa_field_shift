import { useLocale } from '../../shared/locale'
import { useEffect, useRef } from 'react'
import { CheckIcon, ChevronDownIcon, PhotoIcon, Square3Stack3DIcon } from '@heroicons/react/24/outline'
import { basemaps, type BasemapId } from './basemaps'
import type { MoistureSource } from '../../api/contracts'

export type MapLayer = MoistureSource | 'boundaries' | 'forecast' | 'planting' | 'heterogeneity' | 'terrain'

type Props = {
  basemap: BasemapId
  aerialAvailable: boolean
  layers: ReadonlyArray<{ id: MapLayer; label: string }>
  activeLayers: ReadonlySet<MapLayer>
  satelliteActive: boolean
  satelliteAvailable: boolean
  onBasemap: (id: BasemapId) => void
  onLayer: (id: MapLayer) => void
  onSatellite: () => void
}

const basemapIds: BasemapId[] = ['aerial', 'street']

export function MapLayerPicker({
  basemap, aerialAvailable, layers, activeLayers, satelliteActive, satelliteAvailable,
  onBasemap, onLayer, onSatellite,
}: Props) {
  const { t } = useLocale()
  const picker = useRef<HTMLDetailsElement>(null)
  const trigger = useRef<HTMLElement>(null)

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
                disabled={id === 'aerial' && !aerialAvailable}
                title={id === 'aerial' && !aerialAvailable ? t('Aerial imagery is unavailable here') : undefined}
                onClick={() => { onBasemap(id); close() }}>
                <span className={'map-basemap-preview map-preview-' + id} aria-hidden="true">
                  {id === basemap && <CheckIcon className="map-basemap-check" />}
                </span>
                {t(basemaps[id].label)}
              </button>
            ))}
          </div>
        </div>
        <div className="map-overlay-options" role="group" aria-label={t('Map overlay')}>
          <p className="map-layer-heading" aria-hidden="true">{t('Field data')}</p>
          {layers.map(({ id, label }) => (
            <label key={id} className="map-layer-option">
              <input type="checkbox" checked={activeLayers.has(id)} onChange={() => onLayer(id)} />
              <span>{label}</span>
              {id !== 'forecast' && <span className={'map-layer-swatch map-swatch-' + id} aria-hidden="true" />}
            </label>
          ))}
        </div>
        <label className="map-layer-option map-satellite-option">
          <input type="checkbox" checked={satelliteActive} disabled={!satelliteAvailable} onChange={onSatellite} />
          <span>{t('Satellite image by date')}</span>
          <PhotoIcon aria-hidden="true" />
        </label>
      </div>
    </details>
  )
}
