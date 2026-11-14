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

export function spectralColor(value: number, index: SpectralIndex) {
  const [min, max] = spectralDefinitions[index].range
  const colors = ['#936448', '#c19548', '#c4bc72', '#86a06b', '#2b5940']
  return colors[Math.min(4, Math.max(0, Math.floor((value - min) / (max - min) * 5)))]
}
