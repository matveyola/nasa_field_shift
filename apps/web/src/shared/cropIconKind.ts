/** Visual species aliases only; this lookup does not declare model support or suitability. */
export type CropKind = 'maize' | 'soy' | 'wheat' | 'barley' | 'sunflower' | 'rapeseed' | 'peas' | 'sugar_beet' | 'potato' | 'oats' | 'millet'

const aliases: ReadonlyMap<string, CropKind> = new Map([
  ['maize', 'maize'], ['corn', 'maize'], ['maize_grain', 'maize'], ['кукурудза', 'maize'], ['кукурудза на зерно', 'maize'],
  ['soy', 'soy'], ['soybean', 'soy'], ['soybeans', 'soy'], ['соя', 'soy'],
  ['wheat', 'wheat'], ['spring wheat', 'wheat'], ['winter wheat', 'wheat'], ['wheat_spring', 'wheat'],
  ['wheat_winter_frozen', 'wheat'], ['wheat_winter_nonfrozen', 'wheat'],
  ['пшениця', 'wheat'], ['яра пшениця', 'wheat'], ['пшениця яра', 'wheat'], ['озима пшениця', 'wheat'], ['пшениця озима', 'wheat'],
  ['пшениця озима, промерзання ґрунту', 'wheat'], ['пшениця озима, без промерзання', 'wheat'],
  ['barley', 'barley'], ['ячмінь', 'barley'],
  ['sunflower', 'sunflower'], ['соняшник', 'sunflower'],
  ['rapeseed', 'rapeseed'], ['canola', 'rapeseed'], ['rapeseed_canola', 'rapeseed'], ['ріпак', 'rapeseed'],
  ['peas', 'peas'], ['dry peas', 'peas'], ['peas_dry', 'peas'], ['горох', 'peas'], ['горох на сухе зерно', 'peas'],
  ['sugar beet', 'sugar_beet'], ['sugar_beet', 'sugar_beet'], ['цукровий буряк', 'sugar_beet'], ['буряк цукровий', 'sugar_beet'],
  ['potato', 'potato'], ['potatoes', 'potato'], ['картопля', 'potato'],
  ['oats', 'oats'], ['oat', 'oats'], ['овес', 'oats'],
  ['millet', 'millet'], ['просо', 'millet'], ['просо, узагальнений рядок millet', 'millet'],
])

export function cropIconKind(crop?: string): CropKind | undefined {
  return aliases.get((crop ?? '').trim().toLowerCase())
}
