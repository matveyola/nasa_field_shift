import countryBounds from './countryBounds.json' with { type: 'json' }
import regionBounds from './regionBounds.json' with { type: 'json' }
import type { Position } from '../../api/contracts'

export type SetupLocation = { countryCode: string; regionCode: string }
export type SetupRegion = {
  code: string
  uk: string
  en: string
  local?: string
  /** Approximate map navigation extent, not an administrative boundary. */
  bounds: [[number, number], [number, number]]
  maxZoom?: number
}

// Approximate Natural Earth country extents for navigation.
const countryNames = {
  en: new Intl.DisplayNames('en', { type: 'region' }),
  uk: new Intl.DisplayNames('uk', { type: 'region' }),
}
const countryAliases: Record<string, string> = {
  GB: 'UK Great Britain Велика Британія Великобританія',
  US: 'USA United States of America США Сполучені Штати Америки',
  UA: 'Ukraina',
}
type RegionRow = [
  code: string, en: string, uk: string | null, local: string | null,
  west: number, south: number, east: number, north: number,
]
// Natural Earth 1:10m Admin 1 v5.1.1, reduced to labels and rounded navigation extents.
// https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-1-states-provinces/
const administrativeRegions = regionBounds as unknown as Record<string, RegionRow[]>
export const setupCountries = Object.entries(countryBounds).map(([code, bounds]) => ({
  code, en: countryNames.en.of(code) ?? code, uk: countryNames.uk.of(code) ?? code, bounds,
  local: countryAliases[code],
}))

const regionalPresets: Record<string, SetupRegion[]> = {
  UA: [
    { code: 'vinnytsia', uk: 'Вінницька область', en: 'Vinnytsia Oblast', bounds: [[47.9, 27.3], [49.9, 30.1]] },
    { code: 'volyn', uk: 'Волинська область', en: 'Volyn Oblast', bounds: [[50.2, 23.6], [51.95, 26.2]] },
    { code: 'dnipropetrovsk', uk: 'Дніпропетровська область', en: 'Dnipropetrovsk Oblast', bounds: [[47.45, 32.95], [49.2, 36.95]] },
    { code: 'donetsk', uk: 'Донецька область', en: 'Donetsk Oblast', bounds: [[46.85, 36.45], [49.25, 39.15]] },
    { code: 'zhytomyr', uk: 'Житомирська область', en: 'Zhytomyr Oblast', bounds: [[49.55, 27.2], [51.7, 29.8]] },
    { code: 'zakarpattia', uk: 'Закарпатська область', en: 'Zakarpattia Oblast', bounds: [[47.9, 22.1], [49.1, 24.65]] },
    { code: 'zaporizhzhia', uk: 'Запорізька область', en: 'Zaporizhzhia Oblast', bounds: [[46.25, 34.15], [48.15, 37.25]] },
    { code: 'ivano-frankivsk', uk: 'Івано-Франківська область', en: 'Ivano-Frankivsk Oblast', bounds: [[47.7, 23.5], [49.55, 25.7]] },
    { code: 'kyiv', uk: 'Київська область', en: 'Kyiv Oblast', bounds: [[49.15, 29.25], [51.6, 32.2]] },
    { code: 'kirovohrad', uk: 'Кіровоградська область', en: 'Kirovohrad Oblast', bounds: [[47.7, 29.7], [49.3, 33.95]] },
    { code: 'luhansk', uk: 'Луганська область', en: 'Luhansk Oblast', bounds: [[47.75, 37.75], [50.1, 40.25]] },
    { code: 'lviv', uk: 'Львівська область', en: 'Lviv Oblast', bounds: [[48.7, 22.6], [50.7, 25.45]] },
    { code: 'mykolaiv', uk: 'Миколаївська область', en: 'Mykolaiv Oblast', bounds: [[46.35, 30.2], [48.25, 33.2]] },
    { code: 'odesa', uk: 'Одеська область', en: 'Odesa Oblast', bounds: [[45.15, 28.2], [48.25, 31.35]] },
    { code: 'poltava', uk: 'Полтавська область', en: 'Poltava Oblast', bounds: [[48.7, 32.05], [50.6, 35.55]] },
    { code: 'rivne', uk: 'Рівненська область', en: 'Rivne Oblast', bounds: [[50.0, 25.05], [51.95, 27.85]] },
    { code: 'sumy', uk: 'Сумська область', en: 'Sumy Oblast', bounds: [[50.1, 32.9], [52.4, 35.75]] },
    { code: 'ternopil', uk: 'Тернопільська область', en: 'Ternopil Oblast', bounds: [[48.45, 24.7], [50.3, 26.4]] },
    { code: 'kharkiv', uk: 'Харківська область', en: 'Kharkiv Oblast', bounds: [[48.5, 34.8], [50.5, 38.1]] },
    { code: 'kherson', uk: 'Херсонська область', en: 'Kherson Oblast', bounds: [[45.75, 31.5], [47.65, 35.5]] },
    { code: 'khmelnytskyi', uk: 'Хмельницька область', en: 'Khmelnytskyi Oblast', bounds: [[48.4, 26.05], [50.6, 27.9]] },
    { code: 'cherkasy', uk: 'Черкаська область', en: 'Cherkasy Oblast', bounds: [[48.4, 29.55], [50.25, 32.9]] },
    { code: 'chernivtsi', uk: 'Чернівецька область', en: 'Chernivtsi Oblast', bounds: [[47.7, 24.85], [48.7, 27.6]] },
    { code: 'chernihiv', uk: 'Чернігівська область', en: 'Chernihiv Oblast', bounds: [[50.3, 30.45], [52.4, 33.55]] },
    { code: 'crimea', uk: 'Автономна Республіка Крим', en: 'Autonomous Republic of Crimea', bounds: [[44.35, 32.45], [46.25, 36.7]] },
    { code: 'kyiv-city', uk: 'м. Київ', en: 'Kyiv city', bounds: [[50.2, 30.2], [50.7, 30.9]] },
    { code: 'sevastopol', uk: 'м. Севастополь', en: 'Sevastopol city', bounds: [[44.35, 33.35], [44.9, 33.95]] },
  ],
  US: [{ code: 'iowa-demo', uk: 'Айова · Бондурант', en: 'Iowa · Bondurant',
    bounds: [[41.69526025718, -93.42280910459], [41.71673794856, -93.38719090357]], maxZoom: 15 }],
}

export function setupRegions(countryCode: string): SetupRegion[] {
  const country = setupCountries.find(({ code }) => code === countryCode)
  if (!country) return []
  const [west, south, east, north] = country.bounds
  const entireCountry: SetupRegion = {
    code: 'all', uk: 'Уся країна', en: 'Entire country', bounds: [[south, west], [north, east]],
  }
  const administrative = administrativeRegions[countryCode] ?? []
  return [entireCountry, ...(regionalPresets[countryCode] ?? []),
    ...administrative.map(([code, en, uk, local, west, south, east, north]): SetupRegion => ({
      code, uk: uk ?? en, en, local: local ?? undefined, bounds: [[south, west], [north, east]],
    }))]
}

export function setupRegion(location: SetupLocation): SetupRegion | undefined {
  return setupRegions(location.countryCode).find((region) => region.code === location.regionCode)
}

export function setupLocationLabel(location: SetupLocation, language: 'uk' | 'en'): string {
  const country = setupCountries.find(({ code }) => code === location.countryCode)
  const region = setupRegion(location)
  return [country?.[language], region?.code === 'all' ? undefined : region?.[language]].filter(Boolean).join(' · ')
}

function normalizedLocationText(text: string) {
  return text.toLocaleLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '').trim()
}

export function locationMatches(option: { code: string; en: string; uk: string; local?: string }, query: string) {
  const terms = normalizedLocationText(query).split(/\s+/)
  const names = normalizedLocationText([option.en, option.uk, option.local, option.code].filter(Boolean).join(' '))
  return terms.every((term) => names.includes(term))
}

const coordinate = '([+-]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+))'
const latitude = coordinate + '\\s*([NS])?'
const longitude = coordinate + '\\s*([EW])?'
const separatedCoordinates = new RegExp('^' + latitude + '(?:\\s*;\\s*|,\\s+|\\s+)' + longitude + '$', 'i')
const commaCoordinates = new RegExp('^' + latitude.replaceAll('[.,]', '[.]') + ',' + longitude.replaceAll('[.,]', '[.]') + '$', 'i')

/** Coordinate entry follows map conventions: latitude first, longitude second. */
export function parseSetupCoordinates(input: string): Position | undefined {
  const cleaned = input.trim().replace(/[−–—]/g, '-').replace(/°/g, '')
  const parts = cleaned.match(separatedCoordinates) ?? cleaned.match(commaCoordinates)
  if (!parts) return
  const [, latValue, northSouth, lonValue, eastWest] = parts
  if ((northSouth && /^[+-]/.test(latValue)) || (eastWest && /^[+-]/.test(lonValue))) return
  const lat = Number(latValue.replace(',', '.')) * (northSouth?.toUpperCase() === 'S' ? -1 : 1)
  const lon = Number(lonValue.replace(',', '.')) * (eastWest?.toUpperCase() === 'W' ? -1 : 1)
  if (lat >= -80 && lat <= 84 && lon >= -180 && lon <= 180) return [lon, lat]
}
