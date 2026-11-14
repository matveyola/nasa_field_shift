import { useState } from 'react'
import type { FieldProfileSnapshot, SoilHorizon } from '../../api/planningV1.generated'
import { clone } from '../planning/model'
import { tr, type PlanningLocale } from '../planning/i18n'
import { NumberInput, Select, TextInput } from '../planning/controls'

export function FieldProfileEditor({ profile, locale, busy, onSave }: {
  profile: FieldProfileSnapshot; locale: PlanningLocale; busy: boolean; onSave: (draft: FieldProfileSnapshot) => Promise<void>
}) {
  const [draft, setDraft] = useState(() => clone(profile))
  const t = tr(locale)
  const edit = (change: (value: FieldProfileSnapshot) => void) => setDraft(old => { const next = clone(old); change(next); return next })
  const soilFields: [keyof SoilHorizon, string, number | undefined][] = [
    ['top_cm', 'Top (cm)', 1000], ['bottom_cm', 'Bottom (cm)', 1000], ['field_capacity_m3_m3', 'Field capacity (m³/m³)', 1],
    ['wilting_point_m3_m3', 'Wilting point (m³/m³)', 1], ['saturation_m3_m3', 'Saturation (m³/m³)', 1],
    ['sand_fraction', 'Sand fraction', 1], ['silt_fraction', 'Silt fraction', 1], ['clay_fraction', 'Clay fraction', 1],
    ['ph', 'pH', 14], ['organic_carbon_g_kg', 'Organic carbon (g/kg)', 1000], ['salinity_ds_m', 'Salinity (dS/m)', undefined],
  ]
  return <form className="pl-card" onSubmit={e => { e.preventDefault(); void onSave(draft) }}>
    <h2>{t('Field profile')} · {profile.field_id}</h2>
    <p>{t('Profile revision')}: {profile.profile_revision} · {t('Geometry revision')}: {profile.geometry_revision}</p>
    <fieldset disabled={busy} className="pl-form">
      <legend className="sr-only">{t('Field profile')}</legend>
      <div className="pl-row"><TextInput label={t('Current crop')} value={draft.current_crop?.crop_id ?? null} onChange={value => edit(d => { d.current_crop = value ? { crop_id: value, cultivar_id: d.current_crop?.cultivar_id ?? null, profile_version: null } : null })} />
        <TextInput label={t('Cultivar')} value={draft.current_crop?.cultivar_id ?? null} onChange={value => edit(d => { if (d.current_crop) d.current_crop.cultivar_id = value })} />
        <NumberInput label={t('History complete since')} value={draft.history_complete_since} min={1900} max={2200} onChange={value => edit(d => { d.history_complete_since = value })} /></div>
      <h3>{t('History')}</h3>
      {draft.history.map((row, index) => <fieldset className="pl-subcard" key={index}><legend>{t('Season')} {row.season}</legend>
        <div className="pl-row"><NumberInput label={t('Season')} value={row.season} required min={1900} max={2200} onChange={value => edit(d => { if (value !== null) d.history[index].season = value })} />
          <TextInput label={t('Crop')} value={row.crop?.crop_id ?? null} onChange={value => edit(d => {
            d.history[index].crop = value ? { crop_id: value, cultivar_id: row.crop?.cultivar_id ?? null, profile_version: null } : null
            d.history[index].status = value ? 'reported' : 'unknown'
          })} />
          <TextInput label={t('Cultivar')} value={row.crop?.cultivar_id ?? null} onChange={value => edit(d => { if (d.history[index].crop) d.history[index].crop!.cultivar_id = value })} />
          <TextInput label={t('Sowing date')} type="date" value={row.sowing_date} onChange={value => edit(d => { d.history[index].sowing_date = value })} />
          <TextInput label={t('Harvest date')} type="date" value={row.harvest_date} onChange={value => edit(d => { d.history[index].harvest_date = value })} /></div>
        <p>{t('Source')}: {row.source_ids.join(', ') || t('Unknown')}</p>
        <button type="button" className="button subtle" onClick={() => edit(d => { d.history.splice(index, 1) })}>{t('Remove')}</button>
      </fieldset>)}
      <button type="button" className="button subtle" onClick={() => edit(d => { d.history.push({ season: Math.min(...d.history.map(h => h.season), new Date(d.as_of).getUTCFullYear()) - 1, crop: null, status: 'unknown', sowing_date: null, harvest_date: null, source_ids: [] }) })}>{t('Add season')}</button>
      <h3>{t('Soil horizons')}</h3>
      {draft.soil.map((row, index) => <fieldset className="pl-subcard" key={index}><legend>{row.top_cm}–{row.bottom_cm} cm</legend>
        <div className="pl-row">{soilFields.map(([key, label, max]) => <NumberInput key={key} label={t(label)} value={row[key] as number | null} max={max} required={key === 'top_cm' || key === 'bottom_cm'} onChange={value => edit(d => { Object.assign(d.soil[index], { [key]: value }); d.soil[index].quality = 'partial' })} />)}</div>
        <p>{t('Source')}: {row.source_ids.join(', ') || t('Unknown')}</p>
        <button type="button" className="button subtle" onClick={() => edit(d => { d.soil.splice(index, 1) })}>{t('Remove')}</button>
      </fieldset>)}
      <button type="button" className="button subtle" onClick={() => edit(d => {
        const top = d.soil.length ? Math.max(...d.soil.map(h => h.bottom_cm)) : 0
        d.soil.push({ top_cm: top, bottom_cm: top + 30, sand_fraction: null, silt_fraction: null, clay_fraction: null, field_capacity_m3_m3: null, wilting_point_m3_m3: null, saturation_m3_m3: null, organic_carbon_g_kg: null, ph: null, salinity_ds_m: null, source_ids: [], quality: 'partial' })
      })}>{t('Add horizon')}</button>
      <h3>{t('Management')}</h3>
      <div className="pl-row"><Select label={t('Tillage')} value={draft.management.tillage} onChange={value => edit(d => { d.management.tillage = value as typeof d.management.tillage })}>
        {['unknown', 'conventional', 'reduced', 'no_till'].map(v => <option key={v} value={v}>{t(v)}</option>)}</Select>
        <NumberInput label={t('Residue cover fraction')} value={draft.management.residue_cover_fraction} max={1} onChange={value => edit(d => { d.management.residue_cover_fraction = value })} />
        <TextInput label={t('Equipment ID')} value={draft.management.equipment_ids.join(', ')} onChange={value => edit(d => { d.management.equipment_ids = value?.split(',').map(v => v.trim()).filter(Boolean) ?? [] })} /></div>
      {draft.management.irrigation.map((row, index) => <fieldset className="pl-subcard" key={index}><legend>{t('Irrigation')} {row.season}</legend><div className="pl-row">
        <NumberInput label={t('Season')} value={row.season} min={1900} max={2200} required onChange={value => edit(d => { if (value !== null) d.management.irrigation[index].season = value })} />
        <Select label={t('Irrigation system')} value={row.system} onChange={value => edit(d => { d.management.irrigation[index].system = value as typeof row.system })}>
          {['unknown', 'none', 'drip', 'sprinkler', 'surface', 'managed_paddy'].map(v => <option key={v} value={v}>{t(v)}</option>)}</Select>
        <NumberInput label={t('Maximum irrigation (mm)')} value={row.max_mm} onChange={value => edit(d => { d.management.irrigation[index].max_mm = value })} /></div>
        <button type="button" className="button subtle" onClick={() => edit(d => { d.management.irrigation.splice(index, 1) })}>{t('Remove')}</button>
      </fieldset>)}
      <button type="button" className="button subtle" onClick={() => edit(d => { d.management.irrigation.push({ season: (d.management.irrigation.at(-1)?.season ?? new Date(d.as_of).getUTCFullYear()) + 1, max_mm: null, system: 'unknown', source_ids: [] }) })}>{t('Add irrigation season')}</button>
      <button type="submit" className="button primary">{t('Save profile')}</button>
    </fieldset>
  </form>
}
