import { test, expect, type Page } from '@playwright/test'

const now = '2026-11-15T12:00:00Z'
function scope(id: string, revision=0) {return {field_id:id,boundary_revision:revision,geometry_fingerprint:'fixture',generated_at:now,latitude:50,longitude:30,sources:[{provider:'Provider fixture',url:'https://example.test/weather',retrieved_at:now,description:'Synthetic unit fixture, never production data.'}]}}
function weather(id: string, revision=0) {return {...scope(id,revision),timezone:'Europe/Kyiv',hours_complete:true,hours:Array.from({length:24},(_,i)=>({time:new Date(Date.parse(now)+i*3600000).toISOString(),temperature_c:i===0?-2:5,rain_mm:0,gust_ms:8,below_zero:i===0})),forecast_days:Array.from({length:5},(_,i)=>({date:`2026-11-${16+i}`,rain_mm:i+1,rain_probability_percent:i===1||i===3?10:30})),requested_start:'2026-11-16',requested_end:'2026-11-20',missing_dates:[],forecast_rain_mm:15,wettest_dates:['2026-11-20'],lowest_probability_dates:['2026-11-17','2026-11-19']}}
function history(id: string, typical: boolean, incomplete=false) {return {...scope(id),timezone:'UTC',requested_end:'2026-11-14',available_end:'2026-11-12',archive_lag_days:2,limitations:[],periods:[7,30].map(length=>({length_days:length,start:length===7?'2026-11-06':'2026-10-14',end:'2026-11-12',complete:!incomplete,missing_dates:incomplete?['2026-11-10']:[],rain_mm:incomplete?null:length*2,typical_rain_mm:typical?length:null,difference_mm:typical&&!incomplete?length:null,typical_status:typical?'available':'not_requested',typical_periods:typical?[{start:'2025-11-06',end:'2025-11-12',rain_mm:length}]:[]}))}}
async function setup(page: Page, stale=false, withWater=false) {
  await page.clock.install({time:new Date(now)})
  const calls:string[]=[]
  await page.route('**/api/**', async route=>{
    const u=new URL(route.request().url());if(!u.pathname.startsWith('/api/'))return route.continue();calls.push(u.pathname+u.search)
    const parts=u.pathname.split('/');const id=parts[parts.indexOf('fields')+1] ?? 'one'
    let body:unknown=null
    if(u.pathname.endsWith('/weather-summary')) body=weather(id,stale?9:0)
    else if(u.pathname.endsWith('/rainfall-history')) body=history(id,u.searchParams.get('typical')==='true',id==='two')
    else if(u.pathname.endsWith('/seasonal-water')) body={field_id:id,geometry_revision:0,rows:withWater?[{job_id:'source-run',scenario_id:'dry',plan_id:'plan-a',crop_id:'Maize',start_date:'2026-04-01',end_date:'2026-10-01',data_kind:'historical',evidence_sha256:'fixture',demand_mm:200,use_mm:150,deficit_mm:50,drought_days:5,crop_days:100,opening_mm:100,closing_mm:50,irrigation_mm:0,drainage_mm:null,waterlogging_days:3,water_stress_days:null}]:[]}
    else if(u.pathname==='/api/water/forecast'&&withWater) body={analysis:'water_forecast',field_id:u.searchParams.get('field_id'),season:2026,crop:'Maize',initialized_at:now,generated_at:now,method:'fao56',model_version:null,physical_model_version:'fixture-only',provider:'Fixture',source_url:'https://example.test',grid_resolution_km:9,root_depth_m:1,initial_reserve_mm:100,capacity_mm:150,threshold_mm:50,field_capacity:.3,wilting_point:.15,crop_coefficient:1.2,limitations:['Synthetic presentation fixture'],days:[100,45,40].map((reserve_mm,i)=>({date:`2026-11-${15+i}`,reserve_mm,opening_mm:[110,100,45][i],rain_mm:0,irrigation_mm:0,water_use_mm:[10,55,5][i],drainage_mm:0,lead_days:i,root_zone_estimate_mm:null}))}
    else if(u.pathname.endsWith('/jobs')) body=[]
    await route.fulfill({json:body})
  });return calls
}
test('primary weather and water blocks distinguish forecast from dated archive on desktop/mobile',async({page})=>{
  const calls=await setup(page)
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.getByRole('heading',{name:'Field weather',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Water balance',exact:true})).toBeVisible()
  await expect(page.getByText('Next five days, starting tomorrow',{exact:true})).toBeVisible()
  await expect(page.getByText(/Below 0 °C is forecast/)).toBeVisible()
  await expect(page.getByText(/Archive, completed UTC days through/)).toContainText('Nov 12')
  await expect(page.getByText(/No current water estimate/)).toBeVisible()
  await expect(page.locator('.rainfall-periods article').first()).toContainText('Difference from typical')
  expect(calls.filter(s=>s.includes('typical=true'))).toHaveLength(1)
  await expect(page.locator('.weather-hour')).toHaveCount(24)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:test.info().outputPath('weather-water.png'),fullPage:true})
  await page.getByRole('combobox',{name:'Field',exact:true}).selectOption('two')
  await expect(page.locator('.rainfall-periods article').first()).toContainText('Incomplete period; no comparison.')
  await expect(page.locator('.rainfall-periods')).not.toContainText('Difference from typical')
  await expect(page.locator('.field-weather-block').getByRole('link',{name:'Provider fixture'}).first()).not.toBeVisible() // hidden attribution is in disclosures.
})
test('stale boundary weather is never shown as current',async({page})=>{
  await setup(page,true);await page.goto('/tests/analytics/index.html')
  await expect(page.getByText('Forecast belongs to an earlier field boundary. Refresh it.')).toBeVisible()
  await expect(page.locator('.weather-hour')).toHaveCount(0)
})
test('Ukrainian weather labels, units and incomplete water remain distinct',async({page})=>{
  await setup(page);await page.addInitScript(()=>localStorage.setItem('field-shift-language','uk'))
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.getByRole('heading',{name:'Аналітика',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Погода поля',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Водний баланс',exact:true})).toBeVisible()
  await expect(page.locator('.weather-hour').first()).toContainText('м/с')
  await expect(page.getByText(/Немає актуальної оцінки води/)).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})

test('physical water arithmetic and explicit seasonal stress quantities remain separate',async({page})=>{
  await setup(page,false,true);await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.water-today-reading')).toContainText('100 mm')
  await expect(page.locator('.water-reading-line')).toContainText('Daily change: -10 mm')
  await expect(page.locator('.water-result-lines').first()).toContainText('Days below threshold2')
  await page.getByText('Seasonal scenario water estimates',{exact:false}).click()
  const lines=page.locator('.field-water-block .analytics-details-body .water-result-lines').last()
  await expect(lines).toContainText('Waterlogging days3 days')
  await expect(lines).toContainText('Total water-stress daysNo estimate')
  await expect(lines).toContainText('Deficit / demand25 %')
  await expect(lines).toContainText('Drought days / crop days5 %')
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.locator('.water-today-reading')).toHaveCount(0)
  await expect(page.getByText(/No current water estimate/)).toBeVisible()
})

test('a delayed response for the previous field cannot replace current weather',async({page})=>{
  await setup(page)
  let release!:()=>void
  const gate=new Promise<void>(resolve=>{release=resolve})
  let requested!:()=>void
  const started=new Promise<void>(resolve=>{requested=resolve})
  await page.route('**/api/fields/one/weather-summary',async route=>{requested();await gate;await route.fulfill({json:{...weather('one'),hours:weather('one').hours.map(h=>({...h,temperature_c:-40}))}}).catch(()=>{})})
  await page.goto('/tests/analytics/index.html')
  await started
  await page.getByRole('combobox',{name:'Field',exact:true}).selectOption('two')
  await expect(page.locator('.weather-hour').first()).toContainText('-2 °C')
  release()
  await expect(page.locator('.field-weather-block')).not.toContainText('-40 °C')
  await expect(page.getByRole('combobox',{name:'Field',exact:true})).toHaveValue('two')
})
