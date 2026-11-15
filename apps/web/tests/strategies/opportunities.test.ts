import assert from 'node:assert/strict'
import test from 'node:test'
import { calendarPeriodLabel, canReviewAllocation, groupOpportunities, localPlanningDate, matchesOpportunityContext, opportunityGroup, validOpportunityPeriod, validPlanningDate } from '../../src/features/strategies/opportunities.ts'
import { opportunities, opportunity } from './opportunityFixtures.ts'

test('planning date uses local calendar and rejects malformed/normalized impossible dates', () => {
  const now = new Date(2027, 0, 2, 0, 1)
  assert.equal(localPlanningDate(now), '2027-01-02')
  for (const date of ['', '2027-02-29', '2027-04-31', '2027-2-1', 'invalid']) assert.equal(validPlanningDate(date), false)
  assert.equal(validPlanningDate('2028-02-29'), true)
})
test('month sources retain month precision and reviewed sources retain actual day endpoints', () => {
  const period = { start: '2027-06-01', end: '2027-06-30' }
  assert.equal(calendarPeriodLabel(period, 'month', 'en-GB'), 'June 2027')
  assert.equal(calendarPeriodLabel(period, 'day', 'en-GB'), '1 June 2027 – 30 June 2027')
  assert.equal(calendarPeriodLabel(null, 'day', 'uk-UA'), null)
  assert.equal(calendarPeriodLabel(period, 'unknown', 'uk-UA'), null)
})
test('groups distinguish open reviewed windows, typical months, passed and missing data without dropping items', () => {
  const data = opportunities()
  const before = structuredClone(data.items)
  assert.deepEqual(groupOpportunities(data.items).map(group => [group.key, group.items.length]), [['open', 1], ['current_reference_month', 1], ['upcoming', 2], ['passed', 1], ['unknown', 1]])
  assert.deepEqual(data.items, before)
  assert.equal(opportunityGroup(opportunity({ calendar_status: 'open' })), 'current_reference_month')
})
test('allocation handoff requires supported crop, usable calendar and valid maturity harvest year', () => {
  assert.equal(canReviewAllocation(opportunity()), true)
  for (const overrides of [{ harvest_year: null }, { harvest_year: 2101 }, { harvest_year: 2027.5 }, { allocation_supported: false }, { calendar_status: 'passed' as const }, { calendar_status: 'unknown' as const }, { eligibility: 'ineligible' as const }, { eligibility: 'unavailable' as const }, { calendar_kind: 'unavailable' as const }]) assert.equal(canReviewAllocation(opportunity(overrides)), false)
  assert.equal(canReviewAllocation(opportunity({ calendar_status: 'current_reference_month' })), true)
})
test('calendar responses must match field, geometry revision, date and rolling lookahead', () => {
  const data = opportunities()
  assert.equal(matchesOpportunityContext(data, 'north', 1, '2027-06-15', 1), true)
  for (const overrides of [{ field_id: 'south' }, { geometry_revision: 2 }, { planning_date: '2027-06-16' }, { year_count: 6 }]) assert.equal(matchesOpportunityContext({ ...data, ...overrides }, 'north', 1, '2027-06-15', 1), false)
})

test('calendar lookahead respects the endpoint bounds without clipping', () => {
  assert.equal(validOpportunityPeriod('2094-12-31', 6), true)
  for (const [date, years] of [['2095-01-01', 6], ['1899-12-31', 1], ['2100-01-01', 1], ['2027-01-01', 0], ['2027-01-01', 7], ['2027-01-01', 1.5]] as const) assert.equal(validOpportunityPeriod(date, years), false)
  assert.equal(calendarPeriodLabel({ start: 'invalid', end: '2027-06-30' }, 'month', 'en-GB'), null)
  assert.equal(calendarPeriodLabel({ start: '2027-06-30', end: '2027-06-01' }, 'day', 'en-GB'), null)
})
