/** Synthetic presentation harness. Never imported by production. Routes are mocked by Playwright. */
import { createRoot } from 'react-dom/client'
import { useState } from 'react'
import { AnalyticsPage } from './LegacyAnalyticsHarness'
import { LocaleProvider } from '../../src/shared/locale'
import type { AnalyticsSection, MapFocus } from './legacyContext'
import type { Field, Scenario } from '../../src/api/contracts'
import '../../src/shared/styles.css'
import '@fontsource-variable/inter'

const fields: Field[] = ['one', 'two'].map((id, index) => ({ id, name: `Test field ${index + 1}`, crop: 'Maize', area_ha: 12, boundary_revision: 0, planting: null,
  geometry: { type: 'Polygon', coordinates: [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50.01], [30, 50]]] } }))
function Harness() {
  const params = new URLSearchParams(location.search)
  const [fieldId, chooseField] = useState('one')
  const [season, chooseSeason] = useState(2026)
  const [scenario, chooseScenario] = useState<Scenario>('dry_year')
  const [section, onSection] = useState<AnalyticsSection>((params.get('section') ?? 'overview') as AnalyticsSection)
  const [job, showJob] = useState<string>()
  const [action, setAction] = useState('')
  const base = fields.find(f => f.id === fieldId)!
  const field = params.get('crop') === 'unknown' ? { ...base, crop: 'Unknown' } : base
  const openMapLayer = (layer: string, focus?: MapFocus) => setAction(JSON.stringify({ layer, ...focus, fieldId, scenario }))
  return <><AnalyticsPage field={field} fields={fields} context={{ field_id: fieldId, season, scenario }} section={section}
    chooseField={chooseField} chooseSeason={chooseSeason} chooseScenario={chooseScenario} onSection={onSection}
    irrigationAvailable jobId={job} forecastDay={params.get('day') ?? undefined}
    showJob={(id, nextScenario) => { showJob(id); if (nextScenario) chooseScenario(nextScenario) }}
    openFields={() => setAction('fields')} openStrategies={() => setAction(`strategies:${fieldId}:${season}:${scenario}`)}
    openAgent={id => setAction(`agent:${fieldId}:${season}:${scenario}:${id ?? ''}`)} openMapLayer={openMapLayer} />
    <output data-testid="action">{action}</output></>
}
createRoot(document.getElementById('root')!).render(<LocaleProvider><Harness /></LocaleProvider>)
