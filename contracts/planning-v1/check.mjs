// Runtime structural checks use the existing web Ajv dependencies. No network calls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../apps/web/package.json', import.meta.url));
const Ajv = require('ajv/dist/2020.js').default;
const addFormats = require('ajv-formats');
const ajv = new Ajv({ allErrors: true, strict: true, strictTuples: false });
addFormats(ajv);
ajv.addKeyword({ keyword: 'tsType', schemaType: 'string' });
const read = (name) => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
const contract = ajv.compile(read('planning-v1.schema.json'));
const cases = ajv.compile(read('cases.schema.json'));
const empirical = ajv.compile(read('empirical-sites.schema.json'));
const example = read('examples/planning-contracts.json');
assert.ok(contract(example), JSON.stringify(contract.errors));
assert.ok(contract(JSON.parse(JSON.stringify(example))), JSON.stringify(contract.errors));
assert.ok(cases(read('cases.v1.json')), JSON.stringify(cases.errors));
assert.ok(empirical(read('empirical-sites.v1.json')), JSON.stringify(empirical.errors));

for (const season_count of [1, 2, 3]) {
  const bounded = structuredClone(example);
  bounded.request.season_count = season_count;
  assert.ok(contract(bounded), JSON.stringify(contract.errors));
}

const linkedLand = structuredClone(example);
linkedLand.field_profile.land_source_ids = [linkedLand.field_profile.sources[0].id];
assert.ok(contract(linkedLand), JSON.stringify(contract.errors));
linkedLand.field_profile.land_source_ids = [];
assert.ok(contract(linkedLand), JSON.stringify(contract.errors));
// These structural mistakes must fail in a browser/Node consumer too.
for (const mutate of [
  (v) => { v.field_profile.area_ha = '0.79'; },
  (v) => { v.field_profile.land_source_ids = [42]; },
  (v) => { v.field_profile.land_source_ids = null; },
  (v) => { v.field_profile.land_source_ids = Array(201).fill('source'); },
  (v) => { v.field_profile.geometry.coordinates[0][0][1] = 91; },
  (v) => { v.weather.scenarios[0].traces[0].days[0].liquid_rain_mm = -1; },
  (v) => { v.observation.available_at = 'not-a-date'; },
  (v) => { v.apply_request.confirmed_by_human = false; },
  (v) => { v.request.extra_unreviewed_constraint = true; },
  (v) => { v.request.season_count = 4; },
  (v) => { v.evaluation.model.artifact_sha256 = 'not-a-checksum'; },
]) {
  const invalid = structuredClone(example);
  mutate(invalid);
  assert.equal(contract(invalid), false, 'Invalid wire payload was accepted');
}
assert.equal(example.weather.scenarios[0].traces[0].days[0].liquid_rain_mm, 0);
assert.equal(example.evaluation.seasons[0].yield_t_ha.value, null);
console.log('Planning-v1: JSON roundtrip, 24 case shapes, empirical manifest three accepted horizons and 11 invalid payloads checked.');
console.log('Cross-field semantic checks run in Python; no model accuracy is asserted.');
