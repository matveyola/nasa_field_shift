import type { Position } from '../../api/contracts'

export const usgsImageryService = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer'
export const usgsAttribution = 'Map services and data available from <a href="https://www.usgs.gov/programs/national-geospatial-program/national-map">U.S. Geological Survey, National Geospatial Program</a>.'

type MapBounds = [[number, number], [number, number]]
export type AerialProvider = {
  url: string
  attribution: string
  maxNativeZoom: number
  bounds?: MapBounds
  logo?: string
  wmsLayers?: string
}

export const basemaps = {
  aerial: {
    label: 'Satellite',
    url: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg',
    attribution: '<a href="https://cloudless.eox.at/">EOxCloudless</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2016)',
    maxNativeZoom: 14,
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
export const demoAerialBounds: MapBounds = [
  [41.69526025718, -93.42280910459],
  [41.71673794856, -93.38719090357],
]

const usgsAerial = {
  url: usgsImageryService + '/tile/{z}/{y}/{x}',
  attribution: usgsAttribution,
  maxNativeZoom: 16,
}
const spainAerial = {
  url: 'https://www.ign.es/wmts/pnoa-ma?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=OI.OrthoimageCoverage&STYLE=default&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image%2Fjpeg',
  attribution: 'PNOA © <a href="https://pnoa.ign.es/">IGN España</a>',
  maxNativeZoom: 19,
}
const franceAerial = {
  url: 'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image%2Fjpeg',
  attribution: 'Orthophotos © <a href="https://cartes.gouv.fr/">IGN France</a>',
  maxNativeZoom: 19,
}
const nationalAerial: Array<AerialProvider & { bounds: MapBounds }> = [
  { ...usgsAerial, bounds: [[24, -125], [50, -66]] },
  { ...usgsAerial, bounds: [[18, -161], [23, -154]] },
  { ...usgsAerial, bounds: [[51, -179], [72, -129]] },
  { ...franceAerial, bounds: [[44, -5.2], [51.2, 8.4]] },
  { ...franceAerial, bounds: [[42.35, -1.8], [44, 8.4]] },
  { ...franceAerial, bounds: [[41.2, 8.5], [43.1, 9.7]] },
  { ...spainAerial, bounds: [[35.8, -9.5], [43.9, 4.5]] },
  { ...spainAerial, bounds: [[27.5, -18.4], [29.6, -13.3]] },
  {
    bounds: [[50.15, 11.85], [51.72, 15.06]],
    url: 'https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest',
    wmsLayers: 'sn_dop_020',
    attribution: 'Orthophotos © <a href="https://www.geodaten.sachsen.de/">GeoSN</a> · <a href="https://www.govdata.de/dl-de/by-2-0">dl-de/by-2.0</a>',
    maxNativeZoom: 19,
  },
]

export const mapTilerLogo = 'https://api.maptiler.com/resources/logo.svg'
export const placeLabels = {
  url: 'https://tiles.maps.eox.at/wmts/1.0.0/overlay_bright_3857/default/g/{z}/{y}/{x}.png',
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> · Rendering © <a href="https://cloudless.eox.at/documentation/usage">EOX</a> and MapServer',
  maxZoom: 13,
}

export function preferredAerial(position: Position, zoom: number, mapTilerKey?: string): AerialProvider | undefined {
  if (zoom < 12) return undefined
  const [longitude, latitude] = position
  const national = nationalAerial.find(({ bounds }) =>
    latitude >= bounds[0][0] && latitude <= bounds[1][0] &&
    longitude >= bounds[0][1] && longitude <= bounds[1][1])
  if (national) return national
  if (mapTilerKey) return {
    url: 'https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key=' + encodeURIComponent(mapTilerKey),
    attribution: '<a href="https://www.maptiler.com/copyright/">© MapTiler</a>',
    maxNativeZoom: 19,
    logo: mapTilerLogo,
  }
}

export function hasUsgsAerial(position: Position): boolean {
  const [longitude, latitude] = position
  const continental = longitude >= -125 && longitude <= -66 && latitude >= 24 && latitude <= 50
  const hawaii = longitude >= -161 && longitude <= -154 && latitude >= 18 && latitude <= 23
  const alaska = longitude >= -179 && longitude <= -129 && latitude >= 51 && latitude <= 72
  return continental || hawaii || alaska
}

export function coordinateFromMap({ lat, lng }: { lat: number; lng: number }): Position {
  return [lng, lat]
}
