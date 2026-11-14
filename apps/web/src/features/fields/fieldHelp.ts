import type { SpectralIndex } from '../../api/analytics'
import { number } from '../../shared/format.ts'

type HelpLanguage = 'en' | 'uk'

export const spectralHelp = {
  ndvi: {
    en: 'NDVI shows how strongly green vegetation stands out in the image. Compare parts of the same field or follow how the crop changes over time. It is a unitless index and cannot tell you why an area looks different.',
    uk: 'NDVI показує, наскільки виразно на знімку виділяється зелена рослинність. Допомагає порівняти ділянки поля або простежити зміни посіву за сезон. Індекс не має одиниць і не пояснює причину відмінностей.',
  },
  msavi: {
    en: 'MSAVI shows vegetation while reducing the influence of exposed soil. Use it to compare areas where the crop has not closed its rows yet. The index is unitless and does not measure soil moisture.',
    uk: 'MSAVI показує рослинність, зменшуючи вплив відкритого ґрунту на показник. Допомагає порівняти ділянки, де посів ще не зімкнув рядки. Індекс не має одиниць і не вимірює вологість ґрунту.',
  },
  ndre: {
    en: 'NDRE uses a different part of the light spectrum to show variation in green crops. Compare areas of the same crop at a similar growth stage to decide where to scout. The index is unitless and does not diagnose a nutrient shortage.',
    uk: 'NDRE використовує іншу частину спектра світла, щоб показати відмінності в зеленому посіві. Порівнюйте ділянки однієї культури на схожій стадії росту, щоб вибрати місця для огляду. Індекс не має одиниць і не діагностує дефіцит поживних речовин.',
  },
  reci: {
    en: 'RECI uses reflected light as a clue about chlorophyll in the crop. Use differences within the field to choose where to scout. The index is unitless and does not give a measured chlorophyll concentration.',
    uk: 'RECI дає непряму оцінку хлорофілу в посіві за відбитим світлом. За відмінностями в межах поля можна вибрати місця для огляду. Індекс не має одиниць і не дає виміряної концентрації хлорофілу.',
  },
  mcari: {
    en: 'MCARI highlights differences in how strongly crop chlorophyll absorbs light. Use it to find areas to check in the field. It is unitless, and exposed soil or sparse crop cover can affect the reading.',
    uk: 'MCARI підкреслює відмінності в тому, як хлорофіл посіву поглинає світло. Допомагає знайти ділянки для огляду. Індекс не має одиниць, а на значення можуть впливати відкритий ґрунт або розріджений посів.',
  },
  ndmi: {
    en: 'NDMI uses reflected light to show differences associated with moisture in the crop canopy. Use it to choose where to check the crop. It is a unitless index and does not measure soil moisture.',
    uk: 'NDMI показує за відбитим світлом відмінності, пов’язані з вологою рослинного покриву. Допомагає вибрати ділянки для огляду посіву. Індекс не має одиниць і не вимірює вологість ґрунту.',
  },
  ndwi: {
    en: 'NDWI highlights areas that may be open water in the image. Use it to decide where to check for standing water. It is a unitless index and does not measure soil moisture or water depth.',
    uk: 'NDWI виділяє на знімку ділянки, де може бути відкрита вода. Допомагає вибрати місця для перевірки застою води. Індекс не має одиниць і не вимірює вологість ґрунту чи глибину води.',
  },
} satisfies Record<SpectralIndex, Record<HelpLanguage, string>>

export const fieldHelp = {
  en: {
    scene: {
      title: 'Satellite date and coverage',
      description: 'The date is when the satellite captured the image. Coverage is the share of the field with usable pixels, so check it before comparing dates. Clouds, shadows and missing pixels are excluded. Smoothing or zooming changes how the map looks without adding detail.',
    },
    variation: {
      title: 'Within-field variation',
      description: 'The colors show which observed areas have lower, similar or higher NDVI than the field median. Use them to choose places to inspect. The percentage refers to the observed area. The map shows no value in transparent areas.',
    },
    terrain: {
      title: 'Terrain directions',
      description: 'Arrows show the direction of steepest descent so you can pick places to check on the ground. Slope is expressed as a percent (metres of drop per 100 m horizontally) and elevation in metres. Small furrows may be too fine for the model. Arrows cannot confirm runoff or flooding.',
    },
  },
  uk: {
    scene: {
      title: 'Дата знімка й покриття',
      description: 'Дата показує, коли супутник зробив знімок. Покриття — частка поля з придатними пікселями, яку варто перевірити перед порівнянням дат. Хмари, тіні й пікселі без даних відкидаються. Згладжування чи масштабування змінює вигляд карти без додавання деталей.',
    },
    variation: {
      title: 'Відмінності в межах поля',
      description: 'Кольори показують, де NDVI нижчий, близький або вищий за медіану поля. За ними можна вибрати місця для огляду. Відсоток стосується лише спостереженої площі. На прозорих ділянках карта не показує значень.',
    },
    terrain: {
      title: 'Напрямки схилів',
      description: 'Стрілки показують напрямок найкрутішого спуску й допомагають вибрати місця для огляду. Нахил виражають у відсотках (метри спуску на 100 м по горизонталі), висоту — у метрах. Дрібних борозен модель може не бачити. Стрілки не підтверджують стік або затоплення.',
    },
  },
} satisfies Record<HelpLanguage, Record<string, { title: string; description: string }>>

export function sceneHelpMetadata(language: HelpLanguage, metadata: {
  source?: string; date?: string; coveragePercent?: number; analysisResolutionMetres?: number;
}) {
  const uk = language === 'uk'
  const locale = uk ? 'uk-UA' : 'en'
  const unknown = uk ? 'Невідомо' : 'Unknown'
  const { source, date, coveragePercent, analysisResolutionMetres } = metadata
  return [
    { label: uk ? 'Джерело' : 'Source', value: source?.trim() || unknown },
    { label: uk ? 'Дата знімка' : 'Acquisition date', value: date && Number.isFinite(Date.parse(date))
      ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(date)) : unknown },
    { label: uk ? 'Придатне покриття' : 'Usable coverage', value: coveragePercent != null && coveragePercent >= 0 && coveragePercent <= 100
      ? `${number(coveragePercent, 1, locale)}%` : unknown },
    { label: uk ? 'Крок сітки аналізу' : 'Analysis grid spacing', value: analysisResolutionMetres != null && Number.isFinite(analysisResolutionMetres) && analysisResolutionMetres > 0
      ? `${number(analysisResolutionMetres, 1, locale)} ${uk ? 'м' : 'm'}` : unknown },
  ]
}
