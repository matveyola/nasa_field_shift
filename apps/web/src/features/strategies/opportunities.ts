import type { CalendarPeriod, PlantingOpportunities, PlantingOpportunity } from '../../api/plantingOpportunities'
import { allocationSeasonSupported } from './model.ts'

export function localPlanningDate(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
export function validPlanningDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value
}
export function validOpportunityPeriod(date: string, years: number) {
  const year = Number(date.slice(0, 4))
  return validPlanningDate(date) && Number.isInteger(years) && years >= 1 && years <= 6 && year >= 1900 && year + years <= 2100
}
export function matchesOpportunityContext(data: PlantingOpportunities, fieldId: string, revision: number, date: string, years: number) {
  return data.field_id === fieldId && data.geometry_revision === revision && data.planning_date === date && data.year_count === years
}
export function canReviewAllocation(item: PlantingOpportunity) {
  return item.allocation_supported && item.harvest_year !== null && allocationSeasonSupported(item.harvest_year)
    && item.calendar_kind !== 'unavailable' && item.eligibility !== 'ineligible' && item.eligibility !== 'unavailable'
    && ['open', 'current_reference_month', 'upcoming'].includes(item.calendar_status)
}
export function opportunityGroup(item: PlantingOpportunity) {
  if (item.calendar_kind === 'unavailable' || item.calendar_status === 'unknown') return 'unknown'
  // A typical month never becomes an assessed open planting window.
  if (item.calendar_status === 'open' && item.calendar_kind !== 'reviewed_window') return 'current_reference_month'
  return item.calendar_status
}
export const opportunityGroups = ['open', 'current_reference_month', 'upcoming', 'passed', 'unknown'] as const
export function groupOpportunities(items: PlantingOpportunity[]) {
  return opportunityGroups.map(key => ({ key, items: items.filter(item => opportunityGroup(item) === key)
    .sort((a, b) => (a.planting_period?.start ?? '9999').localeCompare(b.planting_period?.start ?? '9999') || a.crop_id.localeCompare(b.crop_id) || a.id.localeCompare(b.id)) }))
}
export function calendarPeriodLabel(period: CalendarPeriod | null, precision: PlantingOpportunity['date_precision'], locale: string) {
  if (!period || precision === 'unknown' || !validPlanningDate(period.start) || !validPlanningDate(period.end) || period.start > period.end) return null
  const options: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'long', timeZone: 'UTC', ...(precision === 'day' ? { day: 'numeric' } : {}) }
  const formatter = new Intl.DateTimeFormat(locale, options)
  const start = formatter.format(new Date(`${period.start}T00:00:00Z`))
  const end = formatter.format(new Date(`${period.end}T00:00:00Z`))
  return start === end ? start : `${start} – ${end}`
}
