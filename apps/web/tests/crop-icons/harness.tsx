/** Test-only vector gallery. Color override demonstrates retained artwork; production CSS stays gray. */
import { createRoot } from 'react-dom/client'
import { CropIcon } from '../../src/shared/CropIcon'
import '../../src/shared/styles.css'
import './preview.css'

const crops = ['wheat', 'maize', 'soy', 'barley', 'sunflower', 'rapeseed', 'peas', 'sugar_beet', 'potato', 'oats', 'millet', 'rice', 'Unknown']
const profiles = ['wheat_winter_frozen','wheat_winter_nonfrozen','wheat_spring','barley','maize_grain','sunflower','soybeans','rapeseed_canola','peas_dry','sugar_beet','potato','oats','millet']
createRoot(document.getElementById('root')!).render(<main className="crop-icon-gallery">
  <h1>Crop vectors</h1><p>Colored artwork and current grayscale presentation. Icons identify crops, not model support.</p>
  <div className="gallery-grid">{crops.map(crop => <article key={crop} data-crop={crop}><h2>{crop}</h2><div className="gallery-pair"><div className="original"><CropIcon crop={crop}/><span>Original</span></div><div className="display"><CropIcon crop={crop}/><span>UI</span></div></div></article>)}</div>
  <h2>Exact catalog identifiers ·24px</h2><div className="catalog-icons">{profiles.map(crop => <div key={crop} data-profile={crop}><CropIcon crop={crop}/><span>{crop}</span></div>)}</div>
  <h2>Recorded rice ·16/24/32px</h2>
  <div className="rice-sizes">{[16,24,32].map(size => <div key={size} data-size={size} style={{'--icon-size': `${size}px`} as React.CSSProperties}>{['Rice','Paddy rice','Рис'].map(crop => <div key={crop} data-rice-alias={crop}><span>{crop} ·{size}px</span><div className="original"><CropIcon crop={crop}/></div><div className="display"><CropIcon crop={crop}/></div></div>)}</div>)}</div>
  <div data-testid="unknown-inputs">{['Unknown','', 'Unknown fixture crop','__proto__'].map(crop => <CropIcon key={crop} crop={crop}/>)}</div>
</main>)
