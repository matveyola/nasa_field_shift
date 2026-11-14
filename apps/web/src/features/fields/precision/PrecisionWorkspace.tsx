import { useCallback, useRef, useState, type FormEvent } from 'react'
import type { Field } from '../../../api/contracts'
import { precisionApi, type PrecisionContext, type PrecisionOperation, type PrecisionPlan } from '../../../api/precision'
import { useAccess } from '../../access/AccessProvider'
import { ComparisonBars } from '../../../shared/DataCharts'
import { useLocale } from '../../../shared/locale'
import { ErrorBox } from '../../../shared/ui'
import { errorText, useResource } from '../../../shared/useResource'
import { PlanPreview } from './PlanPreview'
import { type SceneLayer, zones, zoneLabels } from './zones'

const operations = { fertilization: { label: 'Fertilization', unit: 'kg/ha' }, sowing: { label: 'Sowing', unit: 'seeds/ha' }, spraying: { label: 'Spraying', unit: 'L/ha' } } as const
export function PrecisionWorkspace({ field, season, layer, sceneId }: { field: Field; season: number; layer?: SceneLayer | null; sceneId?: string }) {
  const { t, date } = useLocale()
  const { canEdit } = useAccess()
  const [selected, setSelected] = useState<string>()
  const [creating, setCreating] = useState<'prescriptions' | 'trials'>()
  const resource = useResource(useCallback(async (signal) => {
    const [context, plans] = await Promise.all([precisionApi.context(field.id, season, signal), precisionApi.list(field.id, season, signal)])
    return { context, plans }
  }, [field.id, season, field.boundary_revision]))
  return <div className="precision-workspace">
    <div className="precision-workspace-heading"><strong>{t('Saved maps and field trials')}</strong></div>
    {resource.error && <ErrorBox message={t(resource.error)} />}
    {resource.pending && <p role="status">{t('Loading…')}</p>}
    {resource.data && <>
      <div className="precision-actions">
        <button className="button subtle" disabled={!canEdit || !sceneId || !layer?.cells.length} onClick={() => { setCreating('prescriptions'); setSelected(undefined) }}>{t('New application map')}</button>
        <button className="button subtle" disabled={!canEdit} onClick={() => { setCreating('trials'); setSelected(undefined) }}>{t('New field trial')}</button>
      </div>
      {!canEdit && <p className="analysis-footnote">{t('Your role allows viewing saved maps and reports.')}</p>}
      {!sceneId && <p className="analysis-footnote">{t('Process a satellite scene to create an NDVI application map. Trials use the field boundary.')}</p>}
      {resource.data.plans.length === 0 && !creating && <p className="analysis-description">{t('Save rates and trial designs here to return to them after harvest.')}</p>}
      <ul className="precision-saved-list">{resource.data.plans.map((plan) => <li key={plan.id}>
        <button type="button" className={selected === plan.id ? 'is-selected' : ''} onClick={() => { setCreating(undefined); setSelected(plan.id) }}>
          <span><strong>{plan.name}</strong><small>{date(plan.created_at)} · {t(plan.kind === 'trial' ? 'Field trial' : 'Application map')}</small></span>
          <span className="pill">{t(plan.stale ? 'Boundary changed' : plan.status === 'approved' ? 'Confirmed' : 'Draft')}</span>
        </button>
      </li>)}</ul>
      {creating && <PlanForm key={[creating, sceneId, resource.data.context.geometry_fingerprint].join(':')} kind={creating}
        field={field} season={season} context={resource.data.context} layer={layer} sceneId={sceneId}
        onSaved={(plan) => { setCreating(undefined); setSelected(plan.id); resource.reload() }} onCancel={() => setCreating(undefined)} />}
      {selected && <PlanDetail key={selected} field={field} id={selected} onChange={resource.reload} />}
    </>}
  </div>
}

function PlanForm({ kind, field, season, context, layer, sceneId, onSaved, onCancel }: {
  kind: 'prescriptions' | 'trials'; field: Field; season: number; context: PrecisionContext; layer?: SceneLayer | null; sceneId?: string
  onSaved: (plan: PrecisionPlan) => void; onCancel: () => void
}) {
  const { t } = useLocale()
  const { canEdit } = useAccess()
  const [operation, setOperation] = useState<PrecisionOperation>('fertilization')
  const [count, setCount] = useState(2)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const retry = useRef<{ signature: string; id: string } | undefined>(undefined)
  const unit = operations[operation].unit
  const present = zones.filter((zone) => layer?.cells.some((cell) => cell.zone === zone))
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canEdit || busy) return
    const form = new FormData(event.currentTarget)
    const base = { season, boundary_revision: context.boundary_revision, geometry_fingerprint: context.geometry_fingerprint,
      name: String(form.get('name')).trim(), product: String(form.get('product')).trim(), operation, fallback_rate: Number(form.get('fallback')) }
    const body = kind === 'prescriptions' ? { ...base, observation_date: layer?.date, scene_id: sceneId,
      rates: Object.fromEntries(present.map((zone) => [zone, Number(form.get(zone))])) }
      : { ...base, treatments: Array.from({ length: count }, (_, i) => ({ name: String(form.get('name-' + i)).trim(), rate: Number(form.get('rate-' + i)) })),
        strip_width_m: Number(form.get('width')), heading_degrees: Number(form.get('heading')), headland_m: Number(form.get('headland')),
        replicates: Number(form.get('replicates')), seed: Number(form.get('seed')) }
    const signature = JSON.stringify(body)
    if (retry.current?.signature !== signature) retry.current = { signature, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { onSaved(await precisionApi.create(field.id, kind, { ...body, client_id: retry.current.id })) }
    catch (failure) { setError(errorText(failure)) }
    finally { setBusy(false) }
  }
  return <form className="precision-plan-form" onSubmit={(event) => void submit(event)}>
    <h4>{t(kind === 'trials' ? 'Design a replicated trial' : 'Save an application map')}</h4>
    <div className="precision-controls">
      <label>{t('Name')}<input name="name" required maxLength={100} /></label>
      <label>{t('Product or variety')}<input name="product" required maxLength={100} /></label>
      <label>{t('Operation')}<select value={operation} onChange={(event) => setOperation(event.target.value as PrecisionOperation)}>{Object.entries(operations).map(([value, item]) => <option value={value} key={value}>{t(item.label)}</option>)}</select></label>
      <label>{t('Fallback rate')} · {t(unit)}<input key={operation} name="fallback" type="number" required min={0} max={10000000} step="any" /></label>
    </div>
    <p className="analysis-footnote">{t('Fallback covers headlands and all areas without mapped rates. Enter it explicitly; zero means application off.')}</p>
    {kind === 'prescriptions' ? <div className="precision-controls" key={'zones-' + operation}>{present.map((zone) => <label key={zone}>{t(zoneLabels[zone])} · {t(unit)}<input name={zone} type="number" required min={0} max={10000000} step="any" /></label>)}</div>
      : <>
        <div className="precision-treatments" key={'treatments-' + operation}>{Array.from({ length: count }, (_, i) => <fieldset key={i}>
          <legend>{t(i === 0 ? 'Control' : 'Treatment')} {i + 1}</legend>
          <label>{t('Name')}<input name={'name-' + i} required maxLength={100} defaultValue={t(i === 0 ? 'Control' : 'Treatment') + (i ? ' ' + i : '')} /></label>
          <label>{t('Rate')} · {t(unit)}<input name={'rate-' + i} type="number" min={0} max={10000000} step="any" required /></label>
        </fieldset>)}</div>
        <div className="precision-actions"><button type="button" className="button subtle" disabled={count >= 4} onClick={() => setCount((n) => n + 1)}>{t('Add treatment')}</button>
          {count > 2 && <button type="button" className="button subtle" onClick={() => setCount((n) => n - 1)}>{t('Remove last treatment')}</button>}</div>
        <div className="precision-controls precision-design-grid">
          <label>{t('Implement width, m')}<input type="number" name="width" min={3} max={100} step="any" defaultValue={12} required /></label>
          <label>{t('Heading, degrees from north')}<input type="number" name="heading" min={0} max={179.999} step="any" defaultValue={0} required /></label>
          <label>{t('Headland, m')}<input type="number" name="headland" min={0} max={100} step="any" defaultValue={12} required /></label>
          <label>{t('Replicates')}<input type="number" name="replicates" min={3} max={12} step={1} defaultValue={3} required /></label>
          <label>{t('Randomisation seed')}<input type="number" name="seed" min={0} max={2147483647} step={1} defaultValue={42} required /></label>
        </div>
        <p className="analysis-footnote">{t('Each block contains every treatment in random order. Review strip direction and implement width on the saved preview.')}</p>
      </>}
    {error && <ErrorBox message={t(error)} />}
    <div className="precision-actions"><button type="submit" className="button primary" disabled={busy || !canEdit}>{t(busy ? 'Saving…' : 'Save and preview')}</button><button type="button" className="button subtle" onClick={onCancel} disabled={busy}>{t('Cancel')}</button></div>
  </form>
}

function PlanDetail({ field, id, onChange }: { field: Field; id: string; onChange: () => void }) {
  const { t, number } = useLocale()
  const { canEdit } = useAccess()
  const resource = useResource(useCallback((signal) => precisionApi.detail(field.id, id, signal), [field.id, id]))
  const [confirmed, setConfirmed] = useState(false), [zero, setZero] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function run(action: () => Promise<unknown>, reload = false) {
    setBusy(true); setError('')
    try { await action(); if (reload) { resource.reload(); onChange() } }
    catch (failure) { setError(errorText(failure)) }
    finally { setBusy(false) }
  }
  const plan = resource.data
  const snapshot = plan?.snapshot
  if (resource.error) return <ErrorBox message={t(resource.error)} />
  if (!plan || !snapshot) return <p role="status">{t('Loading…')}</p>
  const hasZero = snapshot.features.some((item) => item.properties.rate === 0)
  const harvest = plan.harvest
  return <section className="precision-plan-detail">
    <h4>{plan.name}</h4><p className="analysis-description">{plan.product} · {t(plan.unit)} · {number(plan.area_ha, 2)} {t('ha')}</p>
    <PlanPreview boundary={snapshot.footprint} features={snapshot.features} />
    <ComparisonBars groups={[{ label: t('Target rates'), unit: t(plan.unit), values: [...new Map(snapshot.features.map((item) => [item.properties.zone, item.properties])).values()].map((item) => ({ label: item.treatment ?? t(item.zone === 'fallback' ? 'Fallback rate' : zoneLabels[item.zone as keyof typeof zoneLabels] ?? item.zone), value: item.rate })) }]} />
    {plan.stale && <p className="error-box">{t('The field geometry changed. This record remains available, but machinery export is blocked. Create a new map.')}</p>}
    {plan.status === 'draft' && <div className="precision-confirm">
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />{t('I checked the geometry, product, rates and units.')}</label>
      {hasZero && <label><input type="checkbox" checked={zero} onChange={(event) => setZero(event.target.checked)} />{t('Zero-rate areas intentionally receive no application.')}</label>}
      <button type="button" className="button primary" disabled={!canEdit || busy || plan.stale || !confirmed || (hasZero && !zero)} onClick={() => void run(() => precisionApi.approve(field.id, id, plan.version, zero), true)}>{t('Confirm map')}</button>
    </div>}
    {plan.status === 'approved' && <>
      <div className="precision-actions">{(['shapefile', 'isoxml', 'geojson'] as const).map((format) => <button className="button subtle" key={format} disabled={plan.stale || busy} onClick={() => void run(() => precisionApi.download(field.id, id, format))}>{format === 'shapefile' ? 'Shapefile · Rx' : format === 'isoxml' ? 'ISOXML · TaskData' : 'GeoJSON'}</button>)}</div>
      <p className="analysis-footnote">{t('On the terminal, verify units and assign the product/channel. ISOXML uses polygon zones; position loss and out-of-field rates are zero. Check compatibility before field use.')}</p>
    </>}
    {plan.kind === 'trial' && plan.status === 'approved' && <div className="precision-harvest">
      <h4>{t('Harvest results')}</h4>
      <label className="precision-file-label">{t('Import calibrated harvest CSV')}<input type="file" accept=".csv,text/csv" disabled={!canEdit || busy} onChange={(event) => {
        const file = event.target.files?.[0]
        if (!file) return
        void run(async () => {
          if (file.size > 1_000_000) throw new Error('Harvest CSV is limited to 1 MB.')
          return precisionApi.harvest(field.id, id, { version: plan.version, file_name: file.name.slice(0, 100), csv: await file.text() })
        }, true)
        event.target.value = ''
      }} /></label>
      <p className="analysis-footnote"><code>longitude,latitude,yield_t_ha,area_ha</code><br />{t('area_ha is optional. Each coordinate must be unique; points on strip borders are excluded. Up to 10,000 samples.')}</p>
      {harvest && <>
        <p className="analysis-description">{plan.harvest_name} · {number(harvest.sample_count, 0)} {t('samples')} · {number(harvest.excluded_samples, 0)} {t('outside trial strips')}</p>
        <PlanPreview boundary={snapshot.footprint} features={snapshot.features} values={new Map(harvest.plots.map((row) => [row.plot_id ?? '', row.yield_t_ha]))} />
        <ComparisonBars groups={[{ label: t('Yield by treatment'), unit: t('t/ha'), values: harvest.treatments.map((row) => ({ label: row.treatment + ' · ' + row.complete_blocks + ' ' + t('blocks'), value: row.yield_t_ha })) }, { label: t('Difference from control'), unit: t('t/ha'), values: harvest.treatments.filter((row) => row.treatment !== harvest.baseline).map((row) => ({ label: row.treatment + ' · ' + row.paired_blocks + ' ' + t('paired blocks'), value: row.difference_t_ha })) }]} />
        <p className="analysis-footnote">{t(harvest.weighting === 'area_within_plot_equal_blocks' ? 'Area-weighted within each plot; equal weight across blocks.' : 'Equal sample weight within each plot; equal weight across blocks.')}{' '}{t('Results describe planned treatments. Confirm actual application and harvest calibration before drawing conclusions.')}</p>
        <button className="button subtle" disabled={busy} onClick={() => void run(() => precisionApi.download(field.id, id, 'csv'))}>{t('Download harvest report CSV')}</button>
      </>}
    </div>}
    {error && <ErrorBox message={t(error)} />}
  </section>
}
