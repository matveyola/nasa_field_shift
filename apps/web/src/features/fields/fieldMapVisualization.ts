import type { SpectralIndex, TerrainResult, VegetationSceneResult } from '../../api/analytics'
import type { MoistureLayer, MoistureSource, Polygon } from '../../api/contracts'
import type { SatelliteRaster } from './satelliteRaster'

export type VisualizationId = MoistureSource | 'none' | 'forecast' | 'heterogeneity' | 'terrain' | 'planting' | 'satellite'

export type FieldMapVisualization =
  | { kind: 'moisture'; layer: MoistureLayer }
  | { kind: 'vegetation'; layer: VegetationSceneResult['heterogeneity']; index: 'relative' | SpectralIndex; geometry: Polygon; colorStops?: { value: number; color: string }[] }
  | { kind: 'terrain'; result: TerrainResult }
  | { kind: 'planting' }
  | { kind: 'satellite'; raster: SatelliteRaster }

export function initialVisualization(ndviLink?: boolean): VisualizationId {
  return ndviLink ? 'heterogeneity' : 'historical'
}

export function toggleVisualization(current: VisualizationId, requested: VisualizationId): VisualizationId {
  return current === requested ? 'none' : requested
}

export function visibleVisualization(active: VisualizationId, hasField: boolean, hasPlanting: boolean): VisualizationId {
  return !hasField || (active === 'planting' && !hasPlanting) ? 'none' : active
}

export function mapRequestResult<K, T>(request: K, response?: { request: K; value: T }) {
  return response?.request === request ? response?.value : undefined
}
