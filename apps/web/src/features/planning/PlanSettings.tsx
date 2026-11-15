import type { CropRef, FarmPlanningRequest, ResourceLimit } from '../../api/planningV1.generated'
import { clone, cropKey, resizePlanningPeriod } from './model'
import { cropName, tr, type PlanningLocale } from './i18n'
import { NumberInput, Select, TextInput } from './controls'

export function PlanSettings({ draft, selectedFields, cropOptions, locale, busy, lockedAtStart, onChange, onFields, onSubmit, replan, compact = false, fieldNames = {}, focus }: {
  compact?: boolean; fieldNames?: Record<string, string>; focus?: 'profiles' | 'economics';
  draft: FarmPlanningRequest; selectedFields: string[]; cropOptions: CropRef[]; locale: PlanningLocale; busy: boolean;
  lockedAtStart: FarmPlanningRequest['locks']; onChange: (value: FarmPlanningRequest) => void;
  onFields: (ids: string[]) => void; onSubmit: () => void; replan: boolean
}) {
  const t = tr(locale)
  const fieldName = (id: string) => fieldNames[id] ?? id
  const seasons = Array.from({ length: draft.season_count }, (_, index) => draft.start_season + index)
  const edit = (fn: (value: FarmPlanningRequest) => void) => { const next = clone(draft); fn(next); onChange(next) }
  const resource = (year: number, patch: Partial<ResourceLimit>) => edit(d => {
    const row = d.resources.find(r => r.season === year)
    if (row) Object.assign(row, patch)
    else d.resources.push({ constraint_id: `resources-${year}`, season: year, budget: null, currency: null, irrigation_m3: null, ...patch })
  })
  return <form className="pl-card" onSubmit={event => { event.preventDefault(); onSubmit() }}><h2>{t('Plan settings')}</h2>
    <fieldset className="pl-form" disabled={busy}><legend className="sr-only">{t('Plan settings')}</legend>
      <fieldset className="pl-row"><legend>{t('Field')}</legend>{draft.fields.map(field => <label className="pl-check" key={field.field_id}>
        <input type="checkbox" checked={selectedFields.includes(field.field_id)} disabled={lockedAtStart.some(l => l.field_id === field.field_id)}
          onChange={event => onFields(event.target.checked ? [...selectedFields, field.field_id] : selectedFields.filter(id => id !== field.field_id))} />{fieldName(field.field_id)} · {field.area_ha} ha</label>)}</fieldset>
      <div className="pl-row"><NumberInput label={t('First harvest year')} value={draft.start_season} min={1900} max={2198} required onChange={value => { if (value !== null) edit(d => { d.start_season = value }) }} />
        <Select label={t('Goal')} value={draft.goal} onChange={value => edit(d => { d.goal = value as typeof d.goal })}>
          <option value="reference_margin">{t('Reference scenario margin')}</option><option value="worst_case_margin">{t('Worst tested margin')}</option><option value="water_demand">{t('Water demand')}</option></Select>
        <Select label={t('Scenario')} value={draft.reference_trajectory_id ?? ''} onChange={value => edit(d => { d.reference_trajectory_id = value || null })}>
          <option value="">{t('Unknown')}</option>{draft.weather.scenarios.map(s => <option key={s.trajectory_id} value={s.trajectory_id}>{s.trajectory_id}</option>)}</Select></div>
      <Select label={t('Planning years')} value={String(draft.season_count)} onChange={value => onChange(resizePlanningPeriod(draft, Number(value) as FarmPlanningRequest['season_count']))}>
        {[1, 2, 3].map(count => <option key={count} value={count} disabled={draft.locks.some(lock => lock.season < draft.start_season || lock.season >= draft.start_season + count)}>{count}</option>)}</Select>
      <p>{t('Planning period')}: {seasons.join(' · ')}</p><p className="planner-help">{t('Changing the horizon removes assumptions outside it; committed seasons remain protected.')}</p>
      <fieldset className="pl-row"><legend>{t('Available crops')}</legend>{cropOptions.map(crop => <label className="pl-check" key={cropKey(crop)}><input type="checkbox"
        checked={draft.enabled_crops.some(c => cropKey(c) === cropKey(crop))}
        disabled={lockedAtStart.some(l => cropKey(l.crop) === cropKey(crop))}
        onChange={event => edit(d => { d.enabled_crops = event.target.checked ? [...d.enabled_crops, crop] : d.enabled_crops.filter(c => cropKey(c) !== cropKey(crop)) })} />
        {cropName(locale, crop.crop_id)} {crop.cultivar_id ?? ''}</label>)}</fieldset>
      <details open={!compact || focus === 'economics'} className="pl-assumptions"><summary>{t('Assumptions, resources and commitments')}</summary>
      <details open={!compact}><summary>{t('Resources')}</summary>{seasons.map(year => {
        const row = draft.resources.find(r => r.season === year)
        return <fieldset key={year} className="pl-subcard"><legend>{year}</legend><div className="pl-row">
          <NumberInput label={t('Budget')} value={row?.budget ?? null} onChange={value => resource(year, { budget: value })} />
          <TextInput label={t('Currency')} value={row?.currency ?? null} onChange={value => resource(year, { currency: value?.toUpperCase() ?? null })} />
          <NumberInput label={t('Irrigation limit (m³)')} value={row?.irrigation_m3 ?? null} onChange={value => resource(year, { irrigation_m3: value })} /></div></fieldset>
      })}</details>
      <details><summary>{t('Crop commitments')}</summary>{draft.fields.filter(f => selectedFields.includes(f.field_id)).flatMap(field => seasons.map(year => {
        const row = draft.locks.find(r => r.field_id === field.field_id && r.season === year)
        const immutable = lockedAtStart.some(r => r.field_id === field.field_id && r.season === year)
        return <div className="pl-subcard" key={`${field.field_id}-${year}`}><Select label={`${fieldName(field.field_id)} · ${year}${immutable ? ` · ${t('Locked')}` : ''}`}
          disabled={immutable} value={row ? cropKey(row.crop) : ''} onChange={value => edit(d => {
            d.locks = d.locks.filter(r => r.field_id !== field.field_id || r.season !== year)
            const crop = d.enabled_crops.find(c => cropKey(c) === value)
            if (crop) d.locks.push({ field_id: field.field_id, season: year, crop, sowing_window: null, harvest_window: null, locked: true, commitment_id: `manual-${field.field_id}-${year}` })
          })}><option value="">{t('No commitment')}</option>{draft.enabled_crops.map(c => <option key={cropKey(c)} value={cropKey(c)}>{cropName(locale, c.crop_id)} {c.cultivar_id}</option>)}</Select>
          {row && <small>{row.commitment_id}</small>}</div>
      }))}</details>
      <details><summary>{t('Crop area limits')}</summary>{draft.crop_areas.map((row, index) => <fieldset className="pl-subcard" key={row.constraint_id}><legend>{row.crop_id} · {row.season}</legend><div className="pl-row">
        <NumberInput label={t('Minimum ha')} value={row.min_ha} required onChange={value => edit(d => { if (value !== null) d.crop_areas[index].min_ha = value })} />
        <NumberInput label={t('Maximum ha')} value={row.max_ha} onChange={value => edit(d => { d.crop_areas[index].max_ha = value })} />
        <NumberInput label={t('Maximum share')} value={row.max_share} max={1} onChange={value => edit(d => { d.crop_areas[index].max_share = value })} /></div>
        <button type="button" className="button subtle" onClick={() => edit(d => { d.crop_areas.splice(index, 1) })}>{t('Remove')}</button></fieldset>)}
        <Select label={t('Crop')} value="" onChange={value => edit(d => {
          const [crop, season] = JSON.parse(value) as [string, number]
          if (!d.crop_areas.some(r => r.crop_id === crop && r.season === season)) d.crop_areas.push({ constraint_id: `crop-${crop}-${season}`, crop_id: crop, season, min_ha: 0, max_ha: null, max_share: null })
        })}><option value="">—</option>{draft.enabled_crops.flatMap(c => seasons.map(year => <option key={`${cropKey(c)}-${year}`} value={JSON.stringify([c.crop_id, year])}>{cropName(locale, c.crop_id)} · {year}</option>))}</Select></details>
      <details><summary>{t('Equipment capacity')}</summary>{draft.equipment.map((row, index) => <fieldset className="pl-subcard" key={row.constraint_id}><legend>{t('Equipment capacity')} {index + 1}</legend><div className="pl-row">
        <TextInput label={t('Equipment ID')} value={row.equipment_id} required onChange={value => edit(d => { d.equipment[index].equipment_id = value ?? '' })} />
        <TextInput label={t('Start date')} type="date" value={row.window.start} required onChange={value => edit(d => { d.equipment[index].window.start = value ?? '' })} />
        <TextInput label={t('End date')} type="date" value={row.window.end} required onChange={value => edit(d => { d.equipment[index].window.end = value ?? '' })} />
        <NumberInput label={t('Capacity (ha)')} value={row.capacity_ha} required onChange={value => edit(d => { if (value !== null) d.equipment[index].capacity_ha = value })} /></div>
        <button type="button" className="button subtle" onClick={() => edit(d => { d.equipment.splice(index, 1) })}>{t('Remove')}</button></fieldset>)}
        <button type="button" className="button subtle" onClick={() => edit(d => { d.equipment.push({ constraint_id: crypto.randomUUID(), equipment_id: '', window: { start: '', end: '' }, capacity_ha: 0 }) })}>{t('Add equipment')}</button></details>
      <details open={focus === 'economics'}><summary>{t('Economics assumptions')}</summary><p>{t('Not net profit')} · {t('No guaranteed profit')}</p>
        <TextInput label={t('Prices and costs share a scenario')} value={draft.economic_correlation_note} required onChange={value => edit(d => { d.economic_correlation_note = value ?? '' })} />
        {draft.economics.map((row, index) => <fieldset className="pl-subcard" key={row.assumption_id}><legend>{cropName(locale, row.crop.crop_id)} · {row.season} · {row.trajectory_id}</legend><div className="pl-row">
          {(['price_per_t', 'variable_cost_per_ha', 'irrigation_cost_per_mm_ha'] as const).map((key, i) => <NumberInput key={key} label={t(['Price / tonne', 'Variable costs excluding irrigation / ha', 'Irrigation cost / mm / ha'][i])} value={row[key]} onChange={value => edit(d => { d.economics[index][key] = value })} />)}
          <TextInput label={t('Currency')} value={row.currency} onChange={value => edit(d => { d.economics[index].currency = value?.toUpperCase() ?? null })} />
          <TextInput label={t('Price date')} type="date" value={row.price_date} onChange={value => edit(d => { d.economics[index].price_date = value })} />
          <TextInput label={t('Source')} value={row.source} required onChange={value => edit(d => { d.economics[index].source = value ?? '' })} />
          <Select label={t('Harvest basis')} value={row.harvest_basis?.basis ?? ''} onChange={value => edit(d => {
            d.economics[index].harvest_basis = value ? { basis: value as 'dry_matter' | 'standard_moisture' | 'fresh_weight', moisture_fraction: value === 'dry_matter' ? 0 : row.harvest_basis?.moisture_fraction ?? Number.NaN } : null
          })}><option value="">{t('Unknown')}</option>{['dry_matter', 'standard_moisture', 'fresh_weight'].map(v => <option key={v} value={v}>{t(v)}</option>)}</Select>
          <NumberInput label={t('Harvest moisture fraction')} value={row.harvest_basis?.moisture_fraction ?? null} max={0.999} required={!!row.harvest_basis} disabled={!row.harvest_basis} onChange={value => edit(d => { if (d.economics[index].harvest_basis) d.economics[index].harvest_basis!.moisture_fraction = value ?? Number.NaN })} />
          <NumberInput label={t('User yield assumption (t/ha)')} value={row.user_yield_t_ha} onChange={value => edit(d => { d.economics[index].user_yield_t_ha = value; d.economics[index].yield_origin = value === null ? 'model' : 'user_assumption' })} /></div>
          <button type="button" className="button subtle" onClick={() => edit(d => { d.economics.splice(index, 1) })}>{t('Remove')}</button></fieldset>)}
        <Select label={t('Economics assumptions')} value="" onChange={value => edit(d => {
          const [key, season, trajectory] = JSON.parse(value) as [string, number, string]
          const crop = d.enabled_crops.find(c => cropKey(c) === key)!
          if (d.economics.some(e => cropKey(e.crop) === key && e.season === season && e.trajectory_id === trajectory)) return
          d.economics.push({ assumption_id: crypto.randomUUID(), crop, season, trajectory_id: trajectory, price_per_t: null, variable_cost_per_ha: null,
            irrigation_cost_per_mm_ha: null, currency: null, price_date: null, harvest_basis: null, source: '', available_at: new Date().toISOString(), user_yield_t_ha: null, yield_origin: 'model' })
        })}><option value="">—</option>{draft.enabled_crops.flatMap(c => seasons.flatMap(year => draft.weather.scenarios.map(s => <option key={`${cropKey(c)}-${year}-${s.trajectory_id}`} value={JSON.stringify([cropKey(c), year, s.trajectory_id])}>{cropName(locale, c.crop_id)} · {year} · {s.trajectory_id}</option>)))}</Select></details>
      </details>
      <button type="submit" className="button primary">{t(replan ? 'Replan' : 'Save and calculate')}</button>
    </fieldset></form>
}
