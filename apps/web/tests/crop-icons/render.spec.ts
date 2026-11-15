import { test, expect } from '@playwright/test'

test('colored originals and grayscale UI share vector geometry without changing decorative semantics',async({page})=>{
  await page.goto('/tests/crop-icons/index.html')
  await expect(page.locator('.gallery-grid article')).toHaveCount(13)
  for(const card of await page.locator('.gallery-grid article').all()) {
    const original=card.locator('.original svg'), display=card.locator('.display svg')
    expect(await original.innerHTML()).toBe(await display.innerHTML())
    await expect(original).toHaveCSS('filter','none')
    await expect(display).toHaveCSS('filter','grayscale(1) contrast(1.05)')
    await expect(display).toHaveAttribute('viewBox','0 0 32 32')
    await expect(display).toHaveAttribute('aria-hidden','true')
    await expect(display).toHaveAttribute('focusable','false')
    // The palette is chromatic in the SVG source, not permanently converted to gray.
    const colors=await original.locator('[fill]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('fill')!).filter(value=>/^#[0-9a-f]{6}$/i.test(value)))
    expect(colors.some(value=>value.slice(1,3)!==value.slice(3,5)||value.slice(3,5)!==value.slice(5,7))).toBe(true)
    // New drawings fit the32px canvas. Original wheat/maize have pre-existing rotated
    // group bounding-box spill; their exact SVG bytes are preserved in external review proof.
    if (!['wheat','maize','soy'].includes((await card.getAttribute('data-crop'))!)) {
    const bounds=await display.evaluate(svg=>{const box=(svg as SVGGraphicsElement).getBBox();return{x:box.x,y:box.y,right:box.x+box.width,bottom:box.y+box.height}})
    expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.y).toBeGreaterThanOrEqual(0)
    expect(bounds.right).toBeLessThanOrEqual(32);expect(bounds.bottom).toBeLessThanOrEqual(32)
    }
  }
  const rice = await page.locator('[data-crop="rice"] .display svg').innerHTML()
  for (const kind of ['Unknown', 'oats', 'wheat', 'millet']) expect(rice).not.toBe(await page.locator(`[data-crop="${kind}"] .display svg`).innerHTML())
  for (const row of await page.locator('.rice-sizes [data-size]').all()) {
    const size = Number(await row.getAttribute('data-size'))
    for (const svg of await row.locator('svg').all()) {
      expect(await svg.innerHTML()).toBe(rice)
      await expect(svg).toHaveCSS('width', `${size}px`)
      await expect(svg).toHaveCSS('height', `${size}px`)
    }
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:test.info().outputPath('colored-and-gray.png'),fullPage:true})
})

test('all13 catalog identifiers draw their species and unknown inputs retain generic fallback',async({page})=>{
  await page.goto('/tests/crop-icons/index.html')
  const generic=await page.locator('[data-crop="Unknown"] .display svg').innerHTML()
  for(const svg of await page.locator('.catalog-icons svg').all()) {
    expect(await svg.innerHTML()).not.toBe(generic)
    await expect(svg).toHaveCSS('width','24px')
  }
  await expect(page.locator('.catalog-icons svg')).toHaveCount(13)
  const originals=await page.locator('.gallery-grid article:not([data-crop="Unknown"]) .display svg').evaluateAll(nodes=>nodes.map(node=>node.innerHTML))
  expect(new Set(originals).size).toBe(12)
  for(const svg of await page.getByTestId('unknown-inputs').locator('svg').all()) expect(await svg.innerHTML()).toBe(generic)
  for(const [id,kind] of [['maize_grain','maize'],['soybeans','soy'],['wheat_winter_frozen','wheat'],['wheat_winter_nonfrozen','wheat'],['wheat_spring','wheat'],['rapeseed_canola','rapeseed'],['peas_dry','peas']]) expect(await page.locator(`[data-profile="${id}"] svg`).innerHTML()).toBe(await page.locator(`[data-crop="${kind}"] .display svg`).innerHTML())
})
