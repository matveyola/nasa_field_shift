import type { SpectralIndex, VegetationObservation, VegetationResult } from '../../api/analytics'

export const spectralDefinitions = {
  ndvi: { formula: '(B08 − B04) / (B08 + B04)', en: 'Vegetation cover', uk: 'Рослинний покрив', range: [-1, 1] },
  msavi: { formula: '(2B08 + 1 − √((2B08 + 1)² − 8(B08 − B04))) / 2', en: 'Vegetation with soil adjustment', uk: 'Рослинність із поправкою на ґрунт', range: [-1.5, 1] },
  ndre: { formula: '(B08 − B05) / (B08 + B05)', en: 'Red-edge vegetation contrast', uk: 'Контраст рослинності в червоному краї', range: [-1, 1] },
  reci: { formula: 'B08 / B05 − 1', en: 'Red-edge chlorophyll proxy', uk: 'Спектральна оцінка хлорофілу', range: [-1, 12] },
  mcari: { formula: '((B05 − B04) − 0.2(B05 − B03)) × B05 / B04', en: 'Chlorophyll absorption proxy', uk: 'Спектральна оцінка поглинання хлорофілу', range: [-1, 2] },
  ndmi: { formula: '(B08 − B11) / (B08 + B11)', en: 'Canopy moisture contrast', uk: 'Контраст вологи рослинного покриву', range: [-1, 1] },
  ndwi: { formula: '(B03 − B08) / (B03 + B08)', en: 'Open-water contrast', uk: 'Контраст відкритої води', range: [-1, 1] },
} satisfies Record<SpectralIndex, { formula: string; en: string; uk: string; range: [number, number] }>

export function spectralMean(observation: VegetationObservation, index: SpectralIndex) {
  return index === 'ndvi' ? observation.mean_ndvi : index === 'msavi' ? observation.mean_msavi : observation.indices?.[index]?.mean
}

export function spectralCell(cell: NonNullable<VegetationResult['heterogeneity']>['cells'][number], index: SpectralIndex) {
  return index === 'ndvi' ? cell.value : index === 'msavi' ? cell.msavi : cell.indices?.[index]
}

// Fixed scales keep dates comparable. Field contrast stretches only the display range.
export function spectralColorStops(index: SpectralIndex, fieldValues?: readonly number[]) {
  const moisture = index === 'ndmi' || index === 'ndwi'
  const colors = moisture
    ? ['#ac572c', '#e9b75f', '#f2edbb', '#76ced1', '#2788b8', '#224b91']
    : ['#d63e32', '#f08b32', '#f3ce45', '#c4df52', '#76c34e', '#299c50', '#087446']
  const [minimum, maximum] = spectralDefinitions[index].range
  const unbounded = index === 'reci' || index === 'mcari'
  const valid = fieldValues?.filter((value) => Number.isFinite(value)
    && (unbounded || (value >= minimum && value <= maximum)))
  if (valid?.length) {
    const min = valid.reduce((a, b) => Math.min(a, b))
    const max = valid.reduce((a, b) => Math.max(a, b))
    if (max - min < 1e-6) return [{ value: min, color: colors[Math.floor(colors.length / 2)] }]
    return colors.map((color, stop) => ({ color, value: min + (max - min) * stop / (colors.length - 1) }))
  }
  if (index === 'ndvi') {
    return [{ value: -1, color: '#a6aab0' }, ...colors.map((color, stop) => ({
      color, value: stop / (colors.length - 1),
    }))]
  }
  return colors.map((color, stop) => ({
    color, value: minimum + (maximum - minimum) * stop / (colors.length - 1),
  }))
}
