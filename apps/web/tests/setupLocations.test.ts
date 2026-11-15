import assert from 'node:assert/strict'
import test from 'node:test'
import { locationMatches, parseSetupCoordinates, setupCountries, setupLocationLabel, setupRegion, setupRegions } from '../src/features/onboarding/locations.ts'

test('countries outside the demo open the map and keep their saved location label', () => {
  for (const countryCode of ['BR', 'FR', 'SG']) {
    const location = { countryCode, regionCode: 'all' }
    const region = setupRegion(location)
    assert.ok(region)
    const [[south, west], [north, east]] = region.bounds
    assert.ok(south < north && west < east)
    assert.equal(setupLocationLabel(location, 'en'), setupCountries.find(({ code }) => code === countryCode)?.en)
  }
})

test('saved Ukrainian oblast and Iowa demo presets still open their own map extent', () => {
  assert.equal(setupRegion({ countryCode: 'UA', regionCode: 'poltava' })?.en, 'Poltava Oblast')
  assert.equal(setupRegion({ countryCode: 'US', regionCode: 'iowa-demo' })?.maxZoom, 15)
})

test('international regions include localized labels and their own navigable extents', () => {
  for (const [countryCode, regionCode, name] of [
    ['FR', 'FR-BRE', 'Bretagne'],
    ['DE', 'DE-BY', 'Bavaria'],
    ['BR', 'BR-SP', 'São Paulo'],
    ['US', 'US-IA', 'Iowa'],
  ]) {
    const region = setupRegion({ countryCode, regionCode })
    assert.equal(region?.en, name)
    assert.notDeepEqual(region.bounds, setupRegions(countryCode)[0].bounds)
  }
  const bavaria = setupRegion({ countryCode: 'DE', regionCode: 'DE-BY' })!
  assert.ok(locationMatches(bavaria, 'баварія'))
  assert.ok(locationMatches(bavaria, 'bayern'))
  const saoPaulo = setupRegion({ countryCode: 'BR', regionCode: 'BR-SP' })!
  assert.ok(locationMatches(saoPaulo, 'sao paulo'))
  assert.ok(locationMatches(setupCountries.find(({ code }) => code === 'US')!, 'США'))
})

test('coordinate paste accepts common formats and keeps GeoJSON longitude order', () => {
  for (const example of ['49.667, 34.557', '49.667,34.557', '49.667 34.557', '49,667; 34,557', '49.667° N 34.557° E']) {
    assert.deepEqual(parseSetupCoordinates(example), [34.557, 49.667], example)
  }
  assert.deepEqual(parseSetupCoordinates('41.695 −93.422'), [-93.422, 41.695])
  assert.deepEqual(parseSetupCoordinates('41.695 N, 93.422 W'), [-93.422, 41.695])
  assert.deepEqual(parseSetupCoordinates('49, 34'), [34, 49])
  assert.deepEqual(parseSetupCoordinates('0,0'), [0, 0])
  for (const example of ['', 'garbage', '90, 45', '45, 181', '1,2,3', '-40N, 30E', '0x29, 1']) {
    assert.equal(parseSetupCoordinates(example), undefined, example)
  }
})
