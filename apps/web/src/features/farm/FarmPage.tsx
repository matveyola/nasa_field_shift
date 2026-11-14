import { useRef, useState } from 'react'
import { AdjustmentsHorizontalIcon, BuildingOffice2Icon, Squares2X2Icon } from '@heroicons/react/24/outline'
import type { Farm, FarmDefaults, Field, Observation } from '../../api/contracts'
import { useLocale } from '../../shared/locale'
import { PageHeading } from '../../shared/ui'
import { FarmOverview } from './FarmOverview'
import { FarmObservations } from './FarmObservations'
import { FarmPriorityList } from './FarmPriorityList'
import { PlanningDefaults } from './PlanningDefaults'
import { TeamPanel } from '../access/TeamPanel'
import './farm.css'

type Props = {
  farm: Farm
  fields: Field[]
  refresh: () => void
  onAddField: () => void
  openField: (id: string) => void
  openVegetation?: (id: string) => void
  openWater?: (fieldId: string, date?: string) => void
  onDefaultsSaved?: (defaults: FarmDefaults) => void
  onObservationSaved?: (observation: Observation) => void
}

export function FarmPage({ farm, fields, refresh, onAddField, openField, openWater, openVegetation, onDefaultsSaved, onObservationSaved }: Props) {
  const { t, number } = useLocale()
  const [view, setView] = useState<'overview' | 'planning'>('overview')
  const [observationEditor, setObservationEditor] = useState<{ fieldId?: string } | null>(null)
  const observationsSection = useRef<HTMLElement>(null)
  function recordObservation(fieldId: string) {
    setObservationEditor((current) => current ?? { fieldId })
    observationsSection.current?.scrollIntoView({ block: 'start' })
  }
  const cropCount = new Set(fields.map(({ crop }) => crop.trim().toLowerCase()).filter(Boolean)).size
  return (
    <div className="page-body farm-page">
      <PageHeading title="Farm management" season={farm.season} dataLabel={null} />
      <div className="farm-workspace-bar">
        <div className="farm-identity"><BuildingOffice2Icon aria-hidden="true" /><strong>{farm.name}</strong></div>
        <div className="farm-view-toggle" role="group" aria-label={t('Farm view')}>
          <button type="button" aria-pressed={view === 'overview'} aria-controls="farm-overview" onClick={() => setView('overview')}><Squares2X2Icon aria-hidden="true" />{t('Overview')}</button>
          <button type="button" aria-pressed={view === 'planning'} aria-controls="farm-planning" onClick={() => setView('planning')}><AdjustmentsHorizontalIcon aria-hidden="true" />{t('Planning')}</button>
        </div>
      </div>
      <div className="farm-content">
        <div id="farm-overview" hidden={view !== 'overview'}>
          <div className="farm-intro">
            <div><h2>{t('Your farm at a glance')}</h2><p>{t('Fields, crops and notes for this season.')}</p></div>
            <span className="chip demo-chip">{t('Demo data')}</span>
          </div>
          <dl className="farm-summary">
            <div><dt>{t('Total area')}</dt><dd>{number(farm.total_area_ha)} <small>{t('ha')}</small></dd></div>
            <div><dt>{t('Fields')}</dt><dd>{number(fields.length, 0)}</dd></div>
            <div><dt>{t('Crops this season')}</dt><dd>{number(cropCount, 0)}</dd></div>
          </dl>
          {view === 'overview' && <FarmPriorityList fields={fields} season={farm.season} openField={openField} openWater={openWater}
            openVegetation={openVegetation} recordObservation={recordObservation} />}
          <FarmOverview fields={fields} openField={openField} onAddField={onAddField} />
          <FarmObservations fields={fields} observations={farm.observations} openField={openField} sectionRef={observationsSection}
            editor={observationEditor} setEditor={setObservationEditor}
            onSaved={(observation) => { if (onObservationSaved) onObservationSaved(observation); else refresh() }} />
        </div>
        <div id="farm-planning" hidden={view !== 'planning'}>
          <PlanningDefaults defaults={farm.defaults} onSaved={(defaults) => { if (onDefaultsSaved) onDefaultsSaved(defaults); else refresh() }} />
        </div>
        <TeamPanel />
      </div>
    </div>
  )
}
