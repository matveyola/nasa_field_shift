import { test } from 'node:test'
import assert from 'node:assert/strict'
import { range, seasonalGroups, waterMetrics } from '../../src/features/analytics/waterMetrics.ts'
import type { SeasonalWaterRow } from '../../src/api/fieldWeather'

function days(values: [string,number][]) { return values.map(([date,reserve_mm]) => ({date,reserve_mm,opening_mm:100,rain_mm:2,irrigation_mm:0,water_use_mm:5,drainage_mm:1})) }
test('strict threshold, missing-day episode reset and flux sums', () => {
  const result=waterMetrics(days([['2026-06-01',50],['2026-06-02',49],['2026-06-03',40],['2026-06-05',30],['2026-06-06',55]]),50)!
  assert.equal(result.firstBelow,'2026-06-02');assert.equal(result.stressDays,3)
  assert.equal(result.minimum?.date,'2026-06-05');assert.equal(result.longest,2)
  assert.equal(result.longestStart,'2026-06-02');assert.equal(result.longestEnd,'2026-06-03')
  assert.equal(result.rain,10);assert.equal(result.use,25);assert.equal(result.irrigation,0);assert.equal(result.drainage,5)
  assert.equal(result.floweringStressDays,null)
})
test('flowering stress requires the supplied confirmed interval; no guessed date',()=>{
  const data=days([['2026-06-01',40],['2026-06-02',40],['2026-06-03',40]])
  assert.equal(waterMetrics(data,50,{start:'2026-06-02',end:'2026-06-02'})?.floweringStressDays,1)
  assert.equal(waterMetrics([...data,data[0]],50),null)
})
test('missing estimates stay null and scenario medians retain failures and zero',()=>{
  assert.equal(range([null,undefined]),null)
  assert.deepEqual(range([0,10,100,null]),{median:10,min:0,max:100,count:3})
  const row:SeasonalWaterRow={job_id:'one',scenario_id:'dry',plan_id:'a',crop_id:'Maize',start_date:'2026-04-01',end_date:'2026-09-01',data_kind:'historical',evidence_sha256:'abc',demand_mm:200,use_mm:150,deficit_mm:50,drought_days:10,crop_days:100,opening_mm:null,closing_mm:null,irrigation_mm:0,drainage_mm:null,waterlogging_days:null,water_stress_days:null}
  const groups=seasonalGroups([row,row,{...row,scenario_id:'wet',deficit_mm:0,drought_days:0},{...row,plan_id:'b'}])
  assert.equal(groups.length,2);assert.equal(groups[0].runs.length,2)
  assert.equal(groups[0].deficitPercent?.median,12.5);assert.equal(groups[0].droughtPercent?.median,5)
  assert.equal(range(groups[0].runs.map(r=>r.water_stress_days)),null)
})
