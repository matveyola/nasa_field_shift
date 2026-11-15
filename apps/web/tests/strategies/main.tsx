/** Synthetic interaction harness. Never imported by production. */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { LegacyStrategiesHarness } from './LegacyStrategiesHarness'
import { LanguageSelect, LocaleProvider } from '../../src/shared/locale'
import type { Field } from '../../src/api/contracts'
import '../../src/shared/styles.css'
import '@fontsource-variable/inter/opsz.css'
const fields = [{ id: 'north', name: 'North field', area_ha: 20, boundary_revision: 1 }, { id: 'south', name: 'South field', area_ha: 35.6, boundary_revision: 1 }] as Field[]
if (new URLSearchParams(location.search).has('long')) fields[0].name = 'ДосліднеПолеПівнічногоГосподарства'.repeat(4)
if (new URLSearchParams(location.search).has('configured')) fields[0].id = 'G23'
function Harness() {
  const [fieldId, setFieldId] = useState(fields[0].id)
  const [season, setSeason] = useState(2027)
  const [action, setAction] = useState('')
  return <LocaleProvider><LanguageSelect /><button onClick={() => setSeason(2028)}>Inspect 2028 externally</button><output>{action}</output><LegacyStrategiesHarness
    field={fields.find(f => f.id === fieldId)!} fields={fields} season={season} onContext={(id, year) => { setFieldId(id); setSeason(year) }}
    openAnalytics={section => setAction(`analytics:${section}`)} openAgent={() => setAction(`agent:${fieldId}:${season}`)} /></LocaleProvider>
}
createRoot(document.getElementById('root')!).render(<Harness />)
