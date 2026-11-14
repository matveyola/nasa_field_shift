import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter/opsz.css' // Inter, the type of «Польовий помічник» and of the whole app
import { App } from './app/App'
import { PhoneFrame, framedPreview, insidePhoneFrame } from './app/PhoneFrame'
import { LocaleProvider } from './shared/locale'
import { AccessProvider } from './features/access/AccessProvider'
import './shared/styles.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root')
if (insidePhoneFrame()) document.documentElement.classList.add('in-phone-frame')

createRoot(root).render(
  <StrictMode>
    {framedPreview()
      ? <PhoneFrame />
      : <LocaleProvider><AccessProvider><App /></AccessProvider></LocaleProvider>}
  </StrictMode>,
)
