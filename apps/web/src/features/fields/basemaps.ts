import type { Position } from '../../api/contracts'

export const usgsImageryService = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer'

export const basemaps = {
  aerial: {
    label: 'Aerial',
    url: usgsImageryService + '/tile/{z}/{y}/{x}',
    attribution: 'Map services and data available from <a href="https://www.usgs.gov/programs/national-geospatial-program/national-map">U.S. Geological Survey, National Geospatial Program</a>.',
    maxNativeZoom: 16,
  },
  street: {
    label: 'Street',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    maxNativeZoom: 19,
  },
} as const

export type BasemapId = keyof typeof basemaps

// Geographic bounds of the USGS Web Mercator image exported for the Bondurant demo area.
export const demoAerialBounds: [[number, number], [number, number]] = [
  [41.69526025718, -93.42280910459],
  [41.71673794856, -93.38719090357],
]

export function defaultBasemap(position?: Position): BasemapId {
  if (!position) return 'street'
  const [longitude, latitude] = position
  const continental = longitude >= -125 && longitude <= -66 && latitude >= 24 && latitude <= 50
  const hawaii = longitude >= -161 && longitude <= -154 && latitude >= 18 && latitude <= 23
  const alaska = longitude >= -179 && longitude <= -129 && latitude >= 51 && latitude <= 72
  return continental || hawaii || alaska ? 'aerial' : 'street'
}

export function coordinateFromMap({ lat, lng }: { lat: number; lng: number }): Position {
  return [lng, lat]
}
