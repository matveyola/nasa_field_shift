import { ChevronRightIcon, MapIcon, PlusIcon } from '@heroicons/react/24/outline'
import type { Field } from '../../api/contracts'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'

const cropColors = ['#0d0d0d', '#5c5c5c', '#8a8a8a', '#a6a6a6', '#c4c4c4', '#dcdcdc']

export function FarmOverview({ fields, openField, onAddField }: {
  fields: Field[]
  openField: (id: string) => void
  onAddField: () => void
}) {
  const { t, number, crop } = useLocale()
  const crops = new Map<string, { name: string; area: number }>()
  for (const field of fields) {
    const key = field.crop.trim().toLowerCase()
    const entry = crops.get(key) ?? { name: field.crop, area: 0 }
    entry.area += field.area_ha
    crops.set(key, entry)
  }
  const composition = [...crops.values()].sort((left, right) => right.area - left.area)
  const totalArea = composition.reduce((sum, item) => sum + item.area, 0)
  const share = (area: number) => totalArea > 0 ? area / totalArea * 100 : 0
  return (
    <div className="farm-overview-grid">
      <section className="farm-section farm-fields" aria-labelledby="farm-fields-heading">
        <div className="farm-section-heading">
          <div><h3 id="farm-fields-heading">{t('Fields')}</h3><p>{t('Open a field to view its map and forecast.')}</p></div>
          <button type="button" className="button primary" onClick={onAddField}><PlusIcon aria-hidden="true" />{t('Add field')}</button>
        </div>
        {fields.length > 0 ? <ul className="farm-field-list">
          {fields.map((field) => <li key={field.id}>
            <button type="button" onClick={() => openField(field.id)}>
              <span className="farm-field-crop"><CropIcon crop={field.crop} /></span>
              <span className="farm-field-description"><strong>{field.name}</strong><span>{crop(field.crop)}</span></span>
              <span className="farm-field-area"><strong>{number(field.area_ha)} <small>{t('ha')}</small></strong><span>{number(share(field.area_ha), 0)}%</span></span>
              <ChevronRightIcon aria-hidden="true" />
              <span className="farm-field-track" aria-hidden="true"><i style={{ width: share(field.area_ha) + '%' }} /></span>
            </button>
          </li>)}
        </ul> : <div className="farm-empty"><MapIcon aria-hidden="true" /><p>{t('Add your first field to start planning.')}</p></div>}
      </section>
      <section className="farm-section farm-crop-mix" aria-labelledby="farm-crop-mix-heading">
        <div className="farm-section-heading"><div><h3 id="farm-crop-mix-heading">{t('Area by crop')}</h3><p>{t('Share of the mapped field area.')}</p></div></div>
        {totalArea > 0 ? <>
          <p className="farm-crop-total"><strong>{number(totalArea)}</strong><span>{t('ha')}</span></p>
          <div className="farm-crop-stack" aria-hidden="true">
            {composition.map((item, index) => <span key={item.name} style={{ width: share(item.area) + '%', background: cropColors[index % cropColors.length] }} />)}
          </div>
          <ul className="farm-crop-legend">
            {composition.map((item, index) => <li key={item.name}>
              <i aria-hidden="true" style={{ background: cropColors[index % cropColors.length] }} />
              <span className="crop-label"><CropIcon crop={item.name} />{crop(item.name)}</span>
              <strong>{number(share(item.area), 0)}%</strong><span>{number(item.area)} {t('ha')}</span>
            </li>)}
          </ul>
        </> : <div className="farm-empty"><p>{t('Crop distribution appears after adding fields.')}</p></div>}
      </section>
    </div>
  )
}
