import assert from 'node:assert/strict'
import test from 'node:test'
import { pages, parseRoute, resolveHomeRoute, routeAddress } from '../src/app/routing.ts'

test('five business destinations keep the assistant central', () => {
  assert.deepEqual(pages, ['fields', 'strategies', 'agent', 'analytics', 'farm'])
})
test('legacy water links retain field, year, scenario and exact saved run in Analytics', () => {
  const route = parseRoute('/water?field=F-03&season=2028&scenario=dry+year&job=42&day=2026-11-12')
  assert.equal(route.page, 'analytics'); assert.equal(route.section, 'water')
  assert.equal(route.fieldId, 'F-03'); assert.equal(route.season, 2028)
  assert.equal(route.scenario, 'dry year'); assert.equal(route.jobId, '42')
  assert.equal(route.forecastDay, undefined)
  assert.deepEqual(parseRoute(routeAddress(route)), route)
  assert.equal(parseRoute(routeAddress({ ...route, page: 'agent' })).jobId, '42')
  assert.equal(parseRoute(routeAddress({ ...route, page: 'farm' })).jobId, undefined)
})
test('legacy planning opens Strategies while old rotation diagnostics stay analytical', () => {
  const route = parseRoute('/rotations?planning=1&field=F-02&season=2028&trajectory=reference')
  assert.equal(route.page, 'strategies')
  assert.equal(route.strategyFocus, 'native')
  assert.equal(parseRoute(routeAddress(route)).strategyFocus, 'native')
  for (const page of pages) {
    const next = parseRoute(routeAddress({ ...route, page }))
    assert.equal(next.fieldId, 'F-02'); assert.equal(next.season, 2028)
    assert.equal(next.trajectory, 'reference')
  }
  assert.equal(parseRoute('/rotations').section, 'rotation')
  assert.equal(parseRoute('/rotations').page, 'analytics')
})
test('vegetation details go to Analytics and map intent preserves exact scene and comparison', () => {
  const diagnostics = parseRoute('/fields?field=F-02&analysis=vegetation&job=old')
  assert.equal(diagnostics.page, 'analytics'); assert.equal(diagnostics.section, 'vegetation')
  assert.equal(diagnostics.jobId, undefined)
  const map = parseRoute('/fields?field=F-02&season=2025&layer=ndvi_variability&observation=2025-05-21&comparison=2025-04-01&index=ndmi&compare=1')
  assert.equal(map.page, 'fields'); assert.equal(map.layer, 'ndvi_variability')
  assert.deepEqual(map.mapFocus, { observationDate: '2025-05-21', comparisonDate: '2025-04-01', index: 'ndmi', comparison: true })
  assert.deepEqual(parseRoute(routeAddress(map)), map)
  assert.equal(parseRoute('/fields?layer=terrain').layer, 'terrain')
  assert.equal(parseRoute(routeAddress({ ...map, page: 'strategies' })).mapFocus, undefined)
})
test('untrusted dates, horizons, sections and indices do not become map or forecast intent', () => {
  assert.equal(parseRoute('/water?day=2026-11-31').forecastDay, undefined)
  assert.equal(parseRoute('/water?day=2026-11-12').forecastDay, '2026-11-12')
  const invalid = parseRoute('/fields?layer=terrain&observation=2026-02-30&index=untrusted&season=NaN&trajectory=%3Cscript%3E')
  assert.equal(invalid.mapFocus, undefined); assert.equal(invalid.season, undefined); assert.equal(invalid.trajectory, undefined)
  assert.equal(parseRoute('/analytics?section=untrusted').section, 'overview')
  assert.equal(parseRoute('/unknown?field=&scenario=').home, true)
  assert.equal(parseRoute('/rotations?season=2201').season, undefined)
})
test('agent alerts and setup links keep only their applicable intents', () => {
  assert.equal(parseRoute('/agent?field=F-01&signal=water').signal, 'water')
  assert.equal(parseRoute('/fields?field=F-01&signal=water').signal, undefined)
  assert.equal(parseRoute('/agent?signal=unknown').signal, undefined)
  assert.equal(parseRoute('/strategies?setup=economics').strategyFocus, 'economics')
  assert.equal(parseRoute('/fields?setup=economics').strategyFocus, undefined)
  assert.equal(parseRoute('/fields?add=1').addField, true)
})

test('onboarding refresh preserves the setup and return field without becoming a planning setup', () => {
  const id = 'fe1d4840-3928-4d02-8a09-67f883e0245b'
  const address = '/onboarding?setup=' + id + '&field=F-03'
  const route = parseRoute(address)
  assert.equal(route.page, 'fields'); assert.equal(route.setupId, id)
  assert.equal(routeAddress(route), address)
  assert.equal(parseRoute('/fields?setup=' + id).setupId, undefined)
  assert.equal(parseRoute('/onboarding?setup=invalid').setupId, undefined)
})
test('farm home leads to strategies while an empty farm starts with fields', () => {
  const home = parseRoute('/?field=F-03&season=2028&scenario=less_rain')
  assert.equal(home.home, true); assert.equal(routeAddress(home), '/?season=2028&field=F-03&scenario=less_rain')
  assert.equal(resolveHomeRoute(home, true).page, 'strategies')
  assert.equal(resolveHomeRoute(home, false).page, 'fields')
  for (const page of pages) {
    const explicit = parseRoute('/' + page + '?field=F-03')
    assert.deepEqual(resolveHomeRoute(explicit, false), explicit)
    assert.deepEqual(resolveHomeRoute(explicit, true), explicit)
  }
})
