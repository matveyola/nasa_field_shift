/** Browser fixture using the production Strategies workspace and intercepted API responses. */
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { Field } from '../../src/api/contracts'
import { StrategiesPage } from '../../src/features/strategies/StrategiesPage'
import { LanguageSelect, LocaleProvider } from '../../src/shared/locale'
import '../../src/shared/styles.css'

const fields = [{ id: 'north', name: 'North field', area_ha: 20, boundary_revision: 4 },
  { id: 'south', name: 'South field', area_ha: 30, boundary_revision: 6 }] as Field[]

function Workflows() {
  const [fieldId, setFieldId] = useState('')
  return <LocaleProvider><LanguageSelect /><StrategiesPage fields={fields} field={fields.find(field => field.id === fieldId)}
    chooseField={setFieldId} openAgent={() => {}} /></LocaleProvider>
}

createRoot(document.getElementById('root')!).render(<Workflows />)
