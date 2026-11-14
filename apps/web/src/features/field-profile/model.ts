import type { FieldProfileSnapshot, ProfileWriteRequest } from '../../api/planningV1.generated'
import { decode } from '../planning/contracts.ts'
import { clone } from '../planning/model.ts'

export async function profileWrite(original: FieldProfileSnapshot, edited: FieldProfileSnapshot, id: string, now: Date): Promise<ProfileWriteRequest> {
  const profile = clone(edited)
  if (profile.field_id !== original.field_id || profile.farm_id !== original.farm_id
    || JSON.stringify(profile.geometry) !== JSON.stringify(original.geometry) || profile.geometry_revision !== original.geometry_revision) throw new Error('Field identity and geometry cannot change in the profile editor')
  if (new Set(profile.history.map(h => h.season)).size !== profile.history.length) throw new Error('Only one history entry per season')
  for (const row of profile.history) {
    if (row.sowing_date && row.harvest_date && row.harvest_date < row.sowing_date) throw new Error('Harvest precedes sowing')
    if (row.status === 'unknown' && row.crop !== null) throw new Error('Unknown history cannot assert a crop')
  }
  const sorted = [...profile.soil].sort((a, b) => a.top_cm - b.top_cm)
  for (const [index, row] of sorted.entries()) {
    if (row.bottom_cm <= row.top_cm || (index > 0 && sorted[index - 1].bottom_cm > row.top_cm)) throw new Error('Soil horizons overlap or have invalid depth')
    const water = [row.wilting_point_m3_m3, row.field_capacity_m3_m3, row.saturation_m3_m3].filter((v): v is number => v !== null)
    if (water.some((v, i) => i > 0 && v < water[i - 1])) throw new Error('Require wilting point ≤ field capacity ≤ saturation')
    const texture = [row.sand_fraction, row.silt_fraction, row.clay_fraction]
    if (texture.every(v => v !== null) && Math.abs(texture.reduce((sum, v) => sum + v!, 0) - 1) > 0.02) throw new Error('Soil texture fractions must sum to 1')
  }
  if (new Set(profile.management.irrigation.map(r => r.season)).size !== profile.management.irrigation.length) throw new Error('Only one irrigation limit per season')
  if (profile.management.irrigation.some(r => r.system === 'none' && r.max_mm !== 0)) throw new Error('No irrigation system requires an explicit zero limit')
  const timestamp = now.toISOString()
  const bytes = new TextEncoder().encode(JSON.stringify({ history: profile.history, soil: profile.soil, management: profile.management, current_crop: profile.current_crop }))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const checksum = [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, '0')).join('')
  const sourceId = `user-${id}`
  profile.sources.push({ id: sourceId, provider: 'Farm user', product: 'Profile edit', version: '1', kind: 'user_entered',
    acquired_at: timestamp, available_at: timestamp, retrieved_at: timestamp, checksum_sha256: checksum,
    licence: 'User supplied farm data', attribution: 'User input; not a laboratory measurement', native_resolution_m: null,
    coverage_fraction: null, spatial_method: 'none', quality: 'partial' })
  const annotate = <T extends { source_ids: string[] }>(rows: T[], before: T[]) => rows.forEach(row => {
    if (!before.some(old => JSON.stringify(old) === JSON.stringify(row))) row.source_ids = [...new Set([...row.source_ids, sourceId])]
  })
  annotate(profile.history, original.history)
  annotate(profile.soil, original.soil)
  annotate(profile.management.irrigation, original.management.irrigation)
  profile.snapshot_id = `profile-${id}`
  profile.profile_revision = original.profile_revision + 1
  profile.as_of = timestamp
  return decode('profile_write', { schema_version: '1.0.0', request_id: id, expected_revision: original.profile_revision, profile })
}
