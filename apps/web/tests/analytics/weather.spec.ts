import { test, expect, type Page } from '@playwright/test'

const now = '2026-11-15T12:00:00Z'
function scope(id: string, revision=0) {return {field_id:id,boundary_revision:revision,geometry_fingerprint:'fixture',generated_at:now,latitude:50,longitude:30,sources:[{provider:'Provider fixture',url:'https://example.test/weather',retrieved_at:now,description:'Synthetic unit fixture, never production data.'}]}}
function weather(id: string, revision=0) {return {...scope(id,revision),timezone:'Europe/Kyiv',hours_complete:true,hours:Array.from({length:24},(_,i)=>({time:new Date(Date.parse(now)+i*3600000).toISOString(),temperature_c:i===0?-2:5,rain_mm:0,gust_ms:8,below_zero:i===0})),forecast_days:Array.from({length:5},(_,i)=>({date:`2026-11-${16+i}`,rain_mm:i+1,rain_probability_percent:i===1||i===3?10:30})),requested_start:'2026-11-16',requested_end:'2026-11-20',missing_dates:[],forecast_rain_mm:15,wettest_dates:['2026-11-20'],lowest_probability_dates:['2026-11-17','2026-11-19']}}
function history(id: string, typical: boolean, incomplete=false) {return {...scope(id),timezone:'UTC',requested_end:'2026-11-14',available_end:'2026-11-12',archive_lag_days:2,limitations:[],periods:[7,30].map(length=>({length_days:length,start:length===7?'2026-11-06':'2026-10-14',end:'2026-11-12',complete:!incomplete,missing_dates:incomplete?['2026-11-10']:[],rain_mm:incomplete?null:length*2,dry_days:incomplete?null:0,typical_rain_mm:typical?length:null,difference_mm:typical&&!incomplete?length:null,typical_status:typical?'available':'not_requested',typical_periods:typical?[{start:'2025-11-06',end:'2025-11-12',rain_mm:length}]:[]}))}}
async function setup(page: Page, stale=false, withWater=false, cold=false) {
  await page.clock.install({time:new Date(now)})
  const calls:string[]=[]
  await page.route('**/api/**', async route=>{
    const u=new URL(route.request().url());if(!u.pathname.startsWith('/api/'))return route.continue();calls.push(u.pathname+u.search)
    const parts=u.pathname.split('/');const id=parts[parts.indexOf('fields')+1] ?? 'one'
    let body:unknown=null
    if(u.pathname.endsWith('/weather-summary')) body=weather(id,stale?9:0)
    else if(u.pathname.endsWith('/rainfall-history')) body=history(id,u.searchParams.get('typical')==='true',id==='two')
    else if(u.pathname.endsWith('/seasonal-water')) body={field_id:id,geometry_revision:0,rows:withWater?[{job_id:'source-run',scenario_id:'dry',plan_id:'plan-a',crop_id:'Maize',start_date:'2026-04-01',end_date:'2026-10-01',data_kind:'historical',evidence_sha256:'fixture',demand_mm:200,use_mm:150,deficit_mm:50,water_shortfall_pct:25,drought_days:5,crop_days:100,opening_mm:100,closing_mm:50,irrigation_mm:0,drainage_mm:null,waterlogging_days:3,water_stress_days:null}]:[]}
    else if(u.pathname==='/api/water/forecast'&&withWater) body={analysis:'water_forecast',field_id:u.searchParams.get('field_id'),season:2026,crop:'Maize',initialized_at:now,generated_at:now,method:'fao56',model_version:null,physical_model_version:'fixture-only',provider:'Fixture',source_url:'https://example.test',grid_resolution_km:9,root_depth_m:1,initial_reserve_mm:100,capacity_mm:150,threshold_mm:50,field_capacity:.3,wilting_point:.15,crop_coefficient:1.2,limitations:['Synthetic presentation fixture'],applicability:cold?{status:'review_required',source_kind:'forecast',source_variable:'soil_temperature_0_to_7cm_mean',depth_cm:[0,7],unit:'°C',aggregation:'daily_mean',initialized_at:now,soil_temperature:[{date:'2026-11-15',mean_c:-2}]}:null,days:[100,45,40].map((reserve_mm,i)=>({date:`2026-11-${15+i}`,reserve_mm,opening_mm:[110,100,45][i],rain_mm:0,irrigation_mm:0,water_use_mm:[10,55,5][i],drainage_mm:0,lead_days:i,root_zone_estimate_mm:null}))}
    else if(u.pathname.endsWith('/jobs')) body=[]
    await route.fulfill({json:body})
  });return calls
}
test('primary weather and water blocks distinguish forecast from dated archive on desktop/mobile',async({page})=>{
  const calls=await setup(page)
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.getByRole('heading',{name:'Field weather',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Water balance',exact:true})).toBeVisible()
  // Full weather evidence now has one named disclosure after the current-field summary.
  await page.getByText('Hourly and daily weather', {exact:true}).click()
  await expect(page.getByText('Rain forecast for 5 days',{exact:true})).toBeVisible()
  await expect(page.getByText(/Below 0 °C is forecast/)).toBeVisible()
  await expect(page.getByText(/Archive, completed UTC days through/)).toContainText('Nov 12')
  await expect(page.locator('.current-field-overview').getByText(/No current water estimate/)).toBeVisible()
  await expect(page.locator('.rainfall-periods article').first()).toContainText('Difference from typical')
  await expect(page.locator('.rainfall-periods article')).toHaveCount(1)
  await expect(page.locator('.rainfall-periods')).toContainText('7 completed days')
  await expect(page.locator('.rainfall-periods')).toContainText('Days with less than 1 mm of rain: 0')
  await page.getByRole('button',{name:'30 days',exact:true}).click()
  await expect(page.locator('.rainfall-periods')).toContainText('30 completed days')
  await expect(page.locator('.rainfall-periods')).toContainText('60 mm')
  expect(calls.filter(s=>s.includes('typical=true'))).toHaveLength(1)
  await expect(page.locator('.weather-hour')).toHaveCount(24)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:test.info().outputPath('weather-water.png'),fullPage:true})
  await page.getByRole('combobox',{name:'Field',exact:true}).selectOption('two')
  await expect(page.locator('.rainfall-periods article').first()).toContainText('Incomplete period; no comparison.')
  await expect(page.locator('.rainfall-periods')).not.toContainText('Difference from typical')
  await expect(page.getByRole('button',{name:'30 days',exact:true})).toHaveAttribute('aria-pressed','true')
  await expect(page.locator('.rainfall-periods')).toContainText('Days with less than 1 mm of rain: Unavailable')
  await expect(page.locator('.rainfall-periods')).not.toContainText('60 mm')
  await expect(page.locator('.field-weather-block').getByRole('link',{name:'Provider fixture'}).first()).not.toBeVisible() // hidden attribution is in disclosures.
})
test('stale boundary weather is never shown as current',async({page})=>{
  await setup(page,true);await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.current-field-overview').getByText('Forecast belongs to an earlier field boundary. Refresh it.', {exact:false})).toBeVisible()
  await expect(page.locator('.weather-hour')).toHaveCount(0)
})
test('Ukrainian weather labels, units and incomplete water remain distinct',async({page})=>{
  await setup(page);await page.addInitScript(()=>localStorage.setItem('field-shift-language','uk'))
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.getByRole('heading',{name:'Аналітика',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Погода поля',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Водний баланс',exact:true})).toBeVisible()
  await page.getByText('Погода за годинами та днями', {exact:true}).click()
  await expect(page.locator('.weather-hour').first()).toContainText('м/с')
  await expect(page.getByRole('heading',{name:'Погода на 24 години',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Прогноз опадів на 5 днів',exact:true})).toBeVisible()
  await expect(page.locator('.current-field-overview').getByText(/Немає актуальної оцінки води/)).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
})

test('physical water arithmetic and explicit seasonal stress quantities remain separate',async({page})=>{
  await setup(page,false,true);await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.water-today-reading')).toContainText('100 mm')
  // Arithmetic moved to evidence, while first stress date/minimum/stress days stay in the lead.
  await page.getByText('Daily calculation and assumptions', {exact:true}).click()
  await expect(page.locator('.water-reading-line')).toContainText('Daily change: -10 mm')
  await expect(page.getByRole('heading',{name:'Water reserve today',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Water movement during the day',exact:true})).toBeVisible()
  await expect(page.locator('.water-reserve-line')).toContainText('Threshold: 50 mm')
  await expect(page.locator('.water-reserve-line')).toContainText('150')
  await expect(page.locator('.water-result-lines').first()).toContainText('Days below threshold2')
  await page.getByText('Water for the season',{exact:false}).click()
  const lines=page.locator('.field-water-block .analytics-details-body .water-result-lines').last()
  await expect(lines).toContainText('Waterlogging days3 days')
  await expect(lines).toContainText('Total water-stress daysNo estimate')
  await expect(lines).toContainText('Deficit / demand25 %')
  await expect(lines).toContainText('Modeled water use150 mm')
  await expect(lines).not.toContainText('Actual water use')
  await expect(lines).toContainText('Drought days / crop days5 %')
  await page.goto('/tests/analytics/index.html?crop=unknown')
  await expect(page.locator('.water-today-reading')).toHaveCount(0)
  await expect(page.locator('.current-field-overview').getByText(/No current water estimate/)).toBeVisible()
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
  await page.getByText('Hourly and daily weather', {exact:true}).click()
  await expect(page.locator('.weather-hour').first()).toContainText('-2 °C')
  release()
  await expect(page.locator('.field-weather-block')).not.toContainText('-40 °C')
  await expect(page.getByRole('combobox',{name:'Field',exact:true})).toHaveValue('two')
})

for (const language of ['en', 'uk'] as const) for (const width of [320, 390, 1440]) {
  test(`compact current state keeps risk and source limits visible at ${width} in ${language}`, async ({page}) => {
    await page.setViewportSize({width,height:844})
    await page.addInitScript(language => localStorage.setItem('field-shift-language',language),language)
    await setup(page,false,true,true)
    await page.goto('/tests/analytics/index.html')
    const text=(en:string,uk:string)=>language==='uk'?uk:en
    const overview=page.locator('.current-field-overview')
    const weatherLead=overview
    const water=page.locator('.field-water-block')
    await expect(weatherLead).toContainText('-2–5 °C')
    await expect(weatherLead).toContainText(text('15 mm','15 мм'))
    await expect(weatherLead).toContainText('Europe/Kyiv')
    await expect(overview.getByText(/Air below 0 °C|повітря нижче 0 °C/)).toBeVisible()
    await expect(overview.locator('.current-reserve')).toContainText(text('100 mm','100 мм'))
    // Leave room for the actual shell header and bottom navigation, not merely a scrolled assertion.
    if (width <= 390) expect((await overview.locator('.current-reserve').boundingBox())!.y + (await overview.locator('.current-reserve').boundingBox())!.height).toBeLessThan(700)
    await expect(page.locator('.field-water-block > details').last().locator('summary').first()).toBeVisible()
    await expect(overview.getByRole('note')).toContainText('0–7')
    await expect(overview.getByRole('note')).toContainText(text('whole root zone','всієї кореневої зони'))
    await expect(overview.getByRole('note')).toContainText(text('not modeled','не моделюються'))
    await expect(overview.locator('.current-next-check')).toContainText('16')
    await expect(page.locator('.weather-hour').first()).not.toBeVisible()
    await expect(page.locator('.weather-daily-list')).not.toBeVisible()
    await expect(page.locator('.water-reserve-line')).not.toBeVisible()
    await expect(page.locator('.water-reading-line')).not.toBeVisible()
    await page.screenshot({path:test.info().outputPath('compact-initial.png'),fullPage:true})
    // The original full-caveat/risk/arithmetic assertions remain, after their new evidence entry.
    await page.getByText(text('Daily calculation and assumptions','Щоденний розрахунок і припущення'),{exact:true}).click()
    await expect(water.locator('.water-today-reading')).toContainText(text('100 mm','100 мм'))
    await expect(water.getByRole('note')).toBeVisible()
    await expect(water.getByRole('note')).toContainText(text('shallow 0–7 cm','поверхневому шарі 0–7 см'))
    await expect(water.getByRole('note')).toContainText(text('does not establish freezing throughout','не доводить промерзання всієї'))
    await expect(water.locator('.water-domain-limit')).toBeVisible()
    const risk=water.locator('.water-result-lines').first()
    await expect(risk).toBeVisible()
    await expect(risk).toContainText(text('First day below threshold','Перший день нижче порога'))
    await expect(risk).toContainText('16')
    await expect(risk).toContainText(text('40 mm','40 мм'))
    await expect(risk).toContainText('17')
    await expect(risk).toContainText(text('Days below threshold2','Днів нижче порога2'))
    await expect(page.locator('.rainfall-period-selector')).toBeVisible()
    await expect(page.locator('.rainfall-periods')).toContainText(text('Difference from typical','Різниця з типовим'))
    const positions=await page.evaluate(()=>({water:document.querySelector('.field-water-block')!.getBoundingClientRect().top,archive:document.querySelector('.field-rain-history')!.getBoundingClientRect().top}))
    expect(positions.water).toBeLessThan(positions.archive)
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    const disclosure=page.locator('.weather-detail > summary')
    await disclosure.focus();await page.keyboard.press('Enter')
    await expect(page.locator('.weather-hour').first()).toBeVisible()
    await expect(page.locator('.weather-hour')).toHaveCount(24)
    await expect(page.locator('.weather-daily-list>div')).toHaveCount(5)
    await expect(page.getByText(text('The rain view requests five days','Огляд опадів запитує п’ять днів'),{exact:false})).toBeVisible()
    await expect(water.getByText(/soil_temperature_0_to_7cm_mean/)).toBeVisible()
    await expect(water.locator('.analytics-table-scroll tbody tr')).toHaveCount(3)
    await expect(water.getByText(text('Synthetic presentation fixture','Synthetic presentation fixture'),{exact:true})).toBeVisible()
    const summaries=await page.locator('.analytics-current-evidence summary').evaluateAll(nodes=>nodes.filter(node=>node.getBoundingClientRect().height>0).map(node=>node.getBoundingClientRect().height))
    expect(summaries.every(height=>height>=44)).toBe(true)
  })
}

test('missing current readings stay unavailable in the compact lead, never zero or complete', async ({page}) => {
  await setup(page)
  await page.route('**/api/fields/one/weather-summary',route=>route.fulfill({json:{...weather('one'),hours_complete:false,hours:weather('one').hours.map(h=>({...h,temperature_c:null,below_zero:null})),forecast_rain_mm:null,missing_dates:['2026-11-18']}}))
  await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.current-temperature')).toContainText('Unavailable')
  await expect(page.locator('.current-rain')).toContainText('Unavailable')
  await expect(page.locator('.current-field-overview')).not.toContainText('0 °C')
  await expect(page.locator('.current-field-overview').getByText('Partial hourly readings; the range uses available temperatures only.')).toBeVisible()
  await expect(page.locator('.current-field-overview').getByText(/Missing forecast dates:/)).toBeVisible()
})

test('forecast retry restores the compact lead without discarding archive selection', async ({page}) => {
  await setup(page)
  let count=0
  await page.route('**/api/fields/one/weather-summary',route=>++count===1?route.fulfill({status:503,json:{detail:'Synthetic provider failure'}}):route.fulfill({json:weather('one')}))
  await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.current-field-overview').getByText('Synthetic provider failure')).toBeVisible()
  await page.getByRole('button',{name:'30 days',exact:true}).click()
  await page.locator('.current-field-overview').getByRole('button',{name:'Try again',exact:true}).click()
  await expect(page.locator('.current-field-overview')).toContainText('15 mm')
  await expect(page.getByRole('button',{name:'30 days',exact:true})).toHaveAttribute('aria-pressed','true')
})

test('hourly headline dates use the returned forecast timezone across UTC midnight', async ({page}) => {
  await setup(page)
  await page.route('**/api/fields/one/weather-summary',route=>route.fulfill({json:{...weather('one'),hours_complete:false,hours:[{time:'2026-11-15T23:00:00Z',temperature_c:0,rain_mm:0,gust_ms:0,below_zero:false},{time:'2026-11-16T00:00:00Z',temperature_c:2,rain_mm:0,gust_ms:0,below_zero:false}]}}))
  await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.current-temperature')).toContainText('16 Nov — 16 Nov · Europe/Kyiv')
  await expect(page.locator('.current-temperature')).toContainText('0–2 °C')
  await expect(page.locator('.current-field-overview').getByText('Partial hourly readings; the range uses available temperatures only.')).toBeVisible()
})

test('one scoped weather request serves summary and evidence; valid zeros survive field changes', async ({page}) => {
  await setup(page,false,true)
  await page.route('**/api/fields/one/weather-summary',route=>route.fulfill({json:{...weather('one'),hours:weather('one').hours.map(h=>({...h,temperature_c:0,below_zero:false})),forecast_rain_mm:0}}))
  let weatherRequests=0
  page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/weather-summary'))weatherRequests++})
  await page.goto('/tests/analytics/index.html')
  const summary=page.locator('.current-field-overview')
  await expect(summary.locator('.current-temperature dd')).toHaveText('0–0 °C')
  await expect(summary.locator('.current-rain dd')).toHaveText('0 mm')
  await expect(summary.locator('.current-reserve dd')).toHaveText('100 mm')
  await page.locator('.weather-detail > summary').click()
  await page.locator('.water-detail > summary').click()
  await expect(page.locator('.weather-hour').first()).toContainText('0 °C')
  await expect(page.locator('.water-today-reading strong')).toHaveText('100 mm')
  expect(weatherRequests).toBe(1)
  await page.getByRole('combobox',{name:'Field',exact:true}).selectOption('two')
  await expect(summary.locator('.current-temperature dd')).toHaveText('-2–5 °C')
  await expect(summary.locator('.current-rain dd')).toHaveText('15 mm')
  expect(weatherRequests).toBe(2)
})

test('current summary and evidence fit doubled text and a narrow preview frame', async ({page}) => {
  const errors: string[]=[]
  page.on('pageerror', error=>errors.push(error.message))
  await setup(page,false,true,true)
  await page.setViewportSize({width:1440,height:1000})
  await page.goto('/tests/analytics/index.html')
  await expect(page.locator('.current-reserve dd')).toHaveText('100 mm')
  await page.locator('body').evaluate(element=>{element.style.zoom='2'})
  const failures=await page.locator('.current-field-values').evaluate(root=>[...root.querySelectorAll('dt,dd')].filter(element=>{
    const box=element.getBoundingClientRect(),parent=element.parentElement!.getBoundingClientRect()
    const range=document.createRange();range.selectNodeContents(element)
    return box.left<parent.left-1||box.right>parent.right+1||[...range.getClientRects()].some(r=>r.left<parent.left-1||r.right>parent.right+1)
  }).map(element=>element.textContent))
  expect(failures).toEqual([])
  await page.locator('body').evaluate(element=>{element.style.zoom=''})
  // Render the component in a320px frame, not a screenshot scaled down.
  await page.evaluate(()=>{document.body.innerHTML='<iframe title="Phone preview" src="/tests/analytics/index.html" style="width:320px;height:844px;border:0"></iframe>'})
  const frame=page.frameLocator('iframe')
  await expect(frame.locator('.current-reserve dd')).toHaveText('100 mm')
  const geometry=await frame.locator('.current-field-values').evaluate(element=>({bottom:element.getBoundingClientRect().bottom,overflow:document.documentElement.scrollWidth>innerWidth}))
  expect(geometry.bottom).toBeLessThan(700)
  expect(geometry.overflow).toBe(false)
  await frame.locator('.water-detail > summary').focus()
  await page.keyboard.press('Enter')
  await expect(frame.locator('.water-today-reading')).toBeVisible()
  await page.screenshot({path:test.info().outputPath('summary-phone-preview.png')})
  expect(errors).toEqual([])
})
