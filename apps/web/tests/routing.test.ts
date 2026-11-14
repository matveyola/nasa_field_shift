import assert from 'node:assert/strict'
import test from 'node:test'
import { pages, parseRoute, resolveHomeRoute, routeAddress } from '../src/app/routing.ts'

test('onboarding links keep the setup and its return field after a refresh', () => {
  const setupId = 'fe1d4840-3928-4d02-8a09-67f883e0245b'
  const address = '/onboarding?setup=' + setupId + '&field=F-03'
  const route = parseRoute(address)
  assert.equal(route.page, 'fields')
  assert.equal(route.setupId, setupId)
  assert.equal(route.fieldId, 'F-03')
  assert.equal(routeAddress(route), address)
  assert.equal(parseRoute('/fields?setup=' + setupId).setupId, undefined)
  const invalid = parseRoute('/onboarding?setup=invalid')
  assert.equal(invalid.page, 'fields')
  assert.equal(invalid.setupId, undefined)
})

test('notification links preserve exact field and signal intent only on the assistant route', () => {
  const route = { page: 'agent' as const, fieldId: 'F-02', scenario: 'baseline', signal: 'water' as const }
  assert.equal(routeAddress(route), '/agent?field=F-02&scenario=baseline&signal=water')
  assert.equal(parseRoute(routeAddress(route)).signal, 'water')
  assert.equal(parseRoute('/agent?field=F-01&signal=vegetation').signal, 'vegetation')
  assert.equal(parseRoute('/agent?field=F-01&signal=unknown').signal, undefined)
  assert.equal(parseRoute('/fields?field=F-01&signal=water').signal, undefined)
  assert.equal(routeAddress({ ...route, page: 'water' }), '/water?field=F-02&scenario=baseline')
})

test('direct links preserve the field, scenario, and water job', () => {
  const route = parseRoute('/water?field=F-03&scenario=dry+year&job=42')
  assert.deepEqual(route, { page: 'water', fieldId: 'F-03', scenario: 'dry year', jobId: '42' })
  assert.equal(routeAddress(route), '/water?field=F-03&scenario=dry+year&job=42')
})

test('the agent can open the same run, and unrelated pages drop the run', () => {
  const water = parseRoute('/water?field=F-03&scenario=dry+year&job=42')
  const agent = routeAddress({ ...water, page: 'agent' })
  assert.equal(agent, '/agent?field=F-03&scenario=dry+year&job=42')
  assert.equal(parseRoute(agent).jobId, water.jobId)
  assert.equal(routeAddress({ ...water, page: 'rotations' }), '/rotations?field=F-03&scenario=dry+year')
})

test('Rotations is the first tab and the home destination when the farm has fields', () => {
  assert.deepEqual(pages, ['rotations', 'fields', 'agent', 'water', 'farm'])
  const home = parseRoute('/')
  assert.equal(home.home, true)
  assert.equal(home.page, 'rotations')
  assert.equal(routeAddress(home), '/')
  assert.equal(resolveHomeRoute(home, true).page, 'rotations')
  assert.equal(resolveHomeRoute(home, false).page, 'fields')
  const homeWithSelection = resolveHomeRoute(parseRoute('/?field=F-03&scenario=less_rain'), true)
  assert.equal(routeAddress(homeWithSelection), '/?field=F-03&scenario=less_rain')
})

test('unknown paths use the same home fallback and discard empty values and unrelated jobs', () => {
  assert.deepEqual(parseRoute('/unknown?field=&scenario=&job=42'), parseRoute('/'))
})

test('home resolution leaves every explicit tab and its field and scenario intact', () => {
  for (const page of pages) {
    const address = '/' + page + '?field=FIELD-42&scenario=dry_year'
    const route = parseRoute(address)
    assert.equal(route.page, page)
    assert.notEqual(route.home, true)
    assert.equal(route.fieldId, 'FIELD-42')
    assert.equal(route.scenario, 'dry_year')
    assert.equal(routeAddress(route), address)
    assert.deepEqual(resolveHomeRoute(route, false), route)
    assert.deepEqual(resolveHomeRoute(route, true), route)
  }
})

test('a map alert can link to one field and one forecast day', () => {
  const route = parseRoute('/water?field=F-02&scenario=base&day=2026-11-12')
  assert.equal(route.fieldId, 'F-02')
  assert.equal(route.forecastDay, '2026-11-12')
  assert.equal(routeAddress(route), '/water?field=F-02&scenario=base&day=2026-11-12')
  assert.equal(routeAddress({ ...route, page: 'fields' }), '/fields?field=F-02&scenario=base')
  assert.equal(routeAddress({ ...route, jobId: '42' }), '/water?field=F-02&scenario=base&job=42')
  assert.equal(parseRoute('/water?job=42&day=2026-11-12').forecastDay, undefined)
  assert.equal(parseRoute('/water?day=2026-11-31').forecastDay, undefined)
})

test('satellite signals link to the exact field analysis and do not carry a water run', () => {
  const route = parseRoute('/fields?field=F-02&analysis=vegetation&job=old-run')
  assert.equal(route.fieldId, 'F-02')
  assert.equal(route.analysis, true)
  assert.equal(route.jobId, undefined)
  assert.equal(routeAddress(route), '/fields?field=F-02&analysis=vegetation')
  assert.equal(parseRoute('/water?analysis=vegetation').analysis, undefined)
  assert.equal(parseRoute('/fields?analysis=unknown').analysis, undefined)
  assert.equal(routeAddress({ ...route, page: 'farm' }), '/farm?field=F-02')
})

test('the assistant can open only the supported NDVI layer in the field analysis', () => {
  const address = '/fields?field=F-02&analysis=vegetation&layer=ndvi_variability'
  const route = parseRoute(address)
  assert.equal(route.fieldId, 'F-02')
  assert.equal(route.layer, 'ndvi_variability')
  assert.equal(routeAddress(route), address)
  assert.equal(parseRoute('/fields?layer=ndvi_variability').layer, undefined)
  assert.equal(parseRoute('/fields?analysis=vegetation&layer=untrusted').layer, undefined)
  assert.equal(routeAddress({ ...route, page: 'agent' }), '/agent?field=F-02')
})
