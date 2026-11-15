import type { FieldProfileSnapshot } from '../../api/planningV1.generated'
import { useAnalyticsCopy } from './copy'
import { rotationEvidence } from './model'

export function ProfileSources({ profile }: { profile: FieldProfileSnapshot }) {
  const { text, date, number } = useAnalyticsCopy()
  return <details className="analytics-disclosure">
    <summary>{text('Profile and source details')}</summary>
    <div className="analytics-details-body">
      <p>{text('Profile as of')} <time dateTime={profile.as_of}>{date(profile.as_of, true)}</time></p>
      <p>{text('A saved profile is not a historical forecast. Check observation dates before using it for another season.')}</p>
      <dl className="analytics-facts"><div><dt>{text('Land status')}</dt><dd>{text(profile.land_status)}</dd></div>
        <div><dt>{text('Profile revision')}</dt><dd>{profile.profile_revision}</dd></div>
        <div><dt>{text('Geometry revision')}</dt><dd>{profile.geometry_revision}</dd></div></dl>
      {profile.missing_data.length > 0 && <ul>{profile.missing_data.map((reason, index) => <li key={index}>{text(reason.message)}</li>)}</ul>}
      <ul className="analytics-source-list">{profile.sources.map(source => <li key={source.id}>
        <strong>{source.provider} · {source.product}</strong><p>{text(source.kind)} · {text(source.quality)} · {source.version}</p>
        <p>{text('Observed')}: {date(source.acquired_at)} · {text('Available since')}: {date(source.available_at)}</p>
        {source.native_resolution_m != null && <p>{number(source.native_resolution_m)} m · {source.spatial_method}</p>}
        <p>{source.attribution} · {source.licence}</p><small>{source.id} · SHA256 {source.checksum_sha256}</small>
      </li>)}</ul>
    </div>
  </details>
}

export function RotationHistory({ profile, season }: { profile?: FieldProfileSnapshot; season: number }) {
  const { text, crop, date } = useAnalyticsCopy()
  const { history, known, repeated } = rotationEvidence(profile, season)
  return <section className="analytics-history" aria-labelledby="analytics-history-heading">
    <h2 id="analytics-history-heading">{text(known.length ? 'What was grown here matters for the next choice' : 'No crop history before this season')}</h2>
    <p>{text(known.length ? 'Recorded history before the selected season; planned sequences are shown separately below.' : 'Missing history cannot be treated as a safe rotation. Record previous crops before choosing the next one.')}</p>
    {repeated.length > 0 && <p className="analytics-caution">{text('Consecutive records repeat the same crop. Review rotation constraints in Strategies; this is not a quantified disease risk.')}</p>}
    {history.length > 0 && <ol className="analytics-history-timeline">{history.map(row => <li key={row.season}>
      <span className="analytics-history-year">{row.season}</span><div><strong>{row.status !== 'unknown' && row.crop ? crop(row.crop.crop_id) : text('Unknown')}</strong>
        <p>{text(row.status === 'confirmed' ? 'Confirmed' : row.status === 'reported' ? 'Reported' : 'Unknown')}</p>
        <small>{row.sowing_date ? `${text('Sowing')}: ${date(row.sowing_date)}` : ''}{row.harvest_date ? ` · ${text('Harvest')}: ${date(row.harvest_date)}` : ''}</small>
        <details><summary>{text('Source')}</summary><p>{row.source_ids.join(' · ') || text('Unknown')}</p></details>
      </div></li>)}</ol>}
    {profile?.history_complete_since != null && <p className="analytics-meta">{text('History completeness starts')}: {profile.history_complete_since}</p>}
  </section>
}

export function SoilProfile({ profile }: { profile?: FieldProfileSnapshot }) {
  const { text, number, date } = useAnalyticsCopy()
  const water = profile?.water_state
  const value = (v: number | null, unit: string, digits = 2) => v == null ? text('Unknown') : `${number(v, digits)} ${unit}`
  return <>
    <section className="analytics-soil-profile" aria-labelledby="analytics-soil-heading">
      <h2 id="analytics-soil-heading">{text('Water storage starts with the soil profile')}</h2>
      <p>{text('Without field capacity and wilting point, available water cannot be inferred from soil depth alone.')}</p>
      <p className="analytics-meta">{text('These are saved inputs, not a new soil measurement or a suitability verdict.')}</p>
      {profile?.soil.length ? <div className="analytics-horizons">{profile.soil.map((horizon, index) => <article className="analytics-horizon" key={index}>
        <div className="analytics-depth"><strong>{horizon.top_cm}–{horizon.bottom_cm}</strong><span>{text('cm')}</span></div>
        <div><header><h3>{text('Recorded soil layers')} {index + 1}</h3><span className="chip">{text(horizon.quality)}</span></header>
          <dl className="analytics-facts"><div><dt>{text('Field capacity')}</dt><dd>{value(horizon.field_capacity_m3_m3, 'm³/m³')}</dd></div>
            <div><dt>{text('Wilting point')}</dt><dd>{value(horizon.wilting_point_m3_m3, 'm³/m³')}</dd></div></dl>
          <details><summary>{text('Texture and chemistry')}</summary><dl className="analytics-facts">
            <div><dt>{text('Sand')}</dt><dd>{value(horizon.sand_fraction == null ? null : horizon.sand_fraction * 100, '%')}</dd></div>
            <div><dt>{text('Clay')}</dt><dd>{value(horizon.clay_fraction == null ? null : horizon.clay_fraction * 100, '%')}</dd></div>
            <div><dt>pH</dt><dd>{value(horizon.ph, '')}</dd></div><div><dt>{text('Organic carbon')}</dt><dd>{value(horizon.organic_carbon_g_kg, 'g/kg')}</dd></div>
          </dl><p className="analytics-meta">{text('Source')}: {horizon.source_ids.join(' · ')}</p></details>
        </div>
      </article>)}</div> : <p className="analytics-empty">{text('soilMissing')}</p>}
    </section>
    {water && <details className="analytics-disclosure"><summary>{text('Recorded water state')}</summary><div className="analytics-details-body">
      <p>{text('Observed')}: {date(water.observed_at, true)} · {text('Available since')}: {date(water.available_at, true)}</p>
      <p>{text('Depth')}: {water.depth_top_cm}–{water.depth_bottom_cm} {text('cm')}</p>
      <dl className="analytics-facts"><div><dt>{text('Volumetric moisture')}</dt><dd>{value(water.volumetric_m3_m3, 'm³/m³')}</dd></div>
        <div><dt>{text('Available reserve')}</dt><dd>{value(water.available_reserve_mm, text('mm'))}</dd></div></dl>
      <p>{text('A moisture fraction and a water reserve describe different quantities and depths.')}</p>
      <p className="analytics-meta">{text('Source')}: {water.source_ids.join(' · ')}</p>
    </div></details>}
  </>
}
