import { useMemo, useRef, useState } from 'react'
import type { Field } from '../../../api/contracts'
import { ComparisonBars } from '../../../shared/DataCharts'
import { useLocale } from '../../../shared/locale'
import { ErrorBox } from '../../../shared/ui'
import { errorText } from '../../../shared/useResource'
import { readMeasurements, summarizeMeasurements, type MeasurementKind } from './measurements'
import { ZonePreview } from './ZonePreview'
import { PrecisionWorkspace } from './PrecisionWorkspace'
import { downloadGeoJson, samplingPoints, zoneAreas, zoneLabels, zones, type SceneLayer } from './zones'
import './precision.css'

export function PrecisionFieldPanel({ field, season, layer, sceneId, fingerprint }: {
  field: Field; season: number; layer?: SceneLayer | null; sceneId?: string; fingerprint?: string
}) {
  const { t } = useLocale()
  const [opened, setOpened] = useState(false)
  return <section className="field-precision">
    <details onToggle={(event) => { if (event.currentTarget.open) setOpened(true) }}>
      <summary>{t('Precision fieldwork')}</summary>
      {opened && <PrecisionWorkspace key={[field.id, season, field.boundary_revision].join(':')} field={field} season={season} layer={layer} sceneId={sceneId} />}
      {opened && (layer?.cells.length && sceneId && fingerprint
        ? <PrecisionDraft key={[field.id, season, field.boundary_revision, fingerprint, sceneId].join(':')}
          field={field} season={season} layer={layer} sceneId={sceneId} fingerprint={fingerprint} />
        : <p className="analysis-empty">{t('Select a processed NDVI scene to work with its zones.')}</p>)}
    </details>
  </section>
}

function PrecisionDraft({ field, season, layer, sceneId, fingerprint }: {
  field: Field; season: number; layer: SceneLayer; sceneId: string; fingerprint: string
}) {
  const { t, date, number } = useLocale()
  const { cells } = layer
  const areas = useMemo(() => zoneAreas(cells), [cells])
  const points = useMemo(() => samplingPoints(cells), [cells])
  const present = zones.filter((zone) => areas[zone] > 0)
  const area = present.reduce((total, zone) => total + areas[zone], 0)
  const provenance = {
    field_id: field.id, season, date: layer.date, scene_id: sceneId,
    boundary_revision: field.boundary_revision, geometry_fingerprint: fingerprint,
    resolution_m: layer.resolution_m, valid_percent: layer.valid_percent,
  }

  return <div className="precision-stack">
    <p className="analysis-description"><time dateTime={layer.date}>{date(layer.date)}</time> · Sentinel‑2 · {number(area, 2)} {t('ha')} {t('mapped area')}</p>
    <ZonePreview boundary={field.geometry} cells={cells} points={points} />
    <ComparisonBars groups={[{ label: t('Mapped area by NDVI zone'), unit: t('ha'),
      values: present.map((zone) => ({ label: t(zoneLabels[zone]), value: areas[zone] })) }]} />
    <details>
      <summary>{t('Soil sampling points')}</summary>
      <p className="analysis-description">{t('One proposed point in each mapped zone. Verify locations and sampling density before taking samples.')}</p>
      <button type="button" className="button subtle analysis-map-toggle" disabled={!points.length} onClick={() =>
        downloadGeoJson(field.id + '-' + layer.date + '-soil-sampling', points.map(({ zone, position }) => ({
          type: 'Feature', geometry: { type: 'Point', coordinates: position }, properties: { ...provenance, zone },
        })))
      }>{t('Download sampling points')}</button>
    </details>
    <MeasurementReport cells={cells} />
  </div>
}

function MeasurementReport({ cells }: { cells: SceneLayer['cells'] }) {
  const { t, number } = useLocale()
  const [kind, setKind] = useState<MeasurementKind>('yield')
  const [report, setReport] = useState<ReturnType<typeof summarizeMeasurements>>()
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const generation = useRef(0)
  const unit = kind === 'yield' ? t('t/ha') : 'pH'
  const groups = report?.rows.filter((row) => row.controlCount > 0 || row.variableCount > 0) ?? []

  async function importFile(file: File) {
    const current = ++generation.current
    setError('')
    setReport(undefined)
    setFileName(file.name)
    try {
      const rows = await readMeasurements(file, kind)
      if (current === generation.current) setReport(summarizeMeasurements(rows, cells))
    } catch (failure) {
      if (current === generation.current) setError(errorText(failure))
    }
  }

  return <details>
    <summary>{t('Yield and soil measurements')}</summary>
    <div className="precision-controls">
      <label><span>{t('Measurement')}</span><select value={kind} onChange={(event) => {
        generation.current++
        setKind(event.target.value as MeasurementKind)
        setReport(undefined)
        setError('')
        setFileName('')
      }}><option value="yield">{t('Harvest yield')}</option><option value="soil">{t('Soil pH')}</option></select></label>
      <label><span>{t('Import measurements CSV')}</span><input type="file" accept=".csv,text/csv" key={kind} onChange={(event) => {
        const file = event.target.files?.[0]
        if (file) void importFile(file)
      }} /></label>
    </div>
    <p className="analysis-footnote"><code>longitude,latitude,{kind === 'yield' ? 'yield_t_ha' : 'ph'}</code>
      {kind === 'yield' && <> · {t('Optional group column: control or variable.')}</>}
    </p>
    {error && <ErrorBox message={t(error)} />}
    {report && <>
      <p className="analysis-description">{fileName} · {number(report.excluded, 0)} {t('points outside mapped zones or on ambiguous borders')}</p>
      <ComparisonBars groups={[{ label: t('Average of imported samples'), unit, values: report.rows.map((row) => ({
        label: t(zoneLabels[row.zone]) + ' (' + number(row.count, 0) + ')', value: row.value,
      })) }]} />
      {groups.length > 0 && <ComparisonBars groups={groups.map((row) => ({
        label: t(zoneLabels[row.zone]), unit,
        values: [{ label: t('Control') + ' (' + number(row.controlCount, 0) + ')', value: row.control },
          { label: t('Variable rate') + ' (' + number(row.variableCount, 0) + ')', value: row.variable }],
      }))} />}
      <p className="analysis-footnote">{t('Each sample has equal weight. Group differences are descriptive. Files are analyzed in this browser session.')}</p>
    </>}
  </details>
}
