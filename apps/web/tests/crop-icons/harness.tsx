/** Test-only vector gallery. Color override demonstrates retained artwork; production CSS stays gray. */
import { createRoot } from 'react-dom/client'
import { CropIcon } from '../../src/shared/CropIcon'
import '../../src/shared/styles.css'
import './preview.css'

const crops = ['wheat', 'maize', 'soy', 'barley', 'sunflower', 'rapeseed', 'peas', 'sugar_beet', 'potato', 'oats', 'millet', 'Unknown']
const profiles = ['wheat_winter_frozen','wheat_winter_nonfrozen','wheat_spring','barley','maize_grain','sunflower','soybeans','rapeseed_canola','peas_dry','sugar_beet','potato','oats','millet']
createRoot(document.getElementById('root')!).render(<main className="crop-icon-gallery">
  <h1>Crop vectors</h1><p>Colored artwork and current grayscale presentation. Icons identify crops, not model support.</p>
  <div className="gallery-grid">{crops.map(crop => <article key={crop} data-crop={crop}><h2>{crop}</h2><div className="gallery-pair"><div className="original"><CropIcon crop={crop}/><span>Original</span></div><div className="display"><CropIcon crop={crop}/><span>UI</span></div></div></article>)}</div>
  <h2>Exact catalog identifiers ·24px</h2><div className="catalog-icons">{profiles.map(crop => <div key={crop} data-profile={crop}><CropIcon crop={crop}/><span>{crop}</span></div>)}</div>
  <div data-testid="unknown-inputs">{['Unknown','', 'Rice','__proto__'].map(crop => <CropIcon key={crop} crop={crop}/>)}</div>
</main>)
