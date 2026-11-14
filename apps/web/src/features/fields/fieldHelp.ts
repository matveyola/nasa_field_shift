import type { SpectralIndex } from '../../api/analytics'
import { number } from '../../shared/format.ts'

type HelpLanguage = 'en' | 'uk'

export const spectralHelp = {
  ndvi: {
    en: 'See where green crops stand out in the satellite image and how they change over the season. Check unusual spots in the field to find out why. NDVI is unitless.',
    uk: 'Подивіться, де зелена рослинність краще виділяється на знімку і як посів змінюється за сезон. Незвичні ділянки огляньте в полі, щоб з’ясувати причину. NDVI не має одиниць.',
  },
  msavi: {
    en: 'Compare young crops while bare soil is still visible between the rows. MSAVI reduces the effect of bare soil on the reading. It is unitless and does not measure soil moisture.',
    uk: 'Порівняйте сходи, поки між рядами ще видно землю. MSAVI зменшує вплив відкритого ґрунту на показник. Він не має одиниць і не вимірює вологість ґрунту.',
  },
  ndre: {
    en: 'Spot differences in crops that have filled out. Compare the same crop at a similar stage of growth. NDRE is unitless. Check the crop in the field before deciding whether it needs fertiliser.',
    uk: 'Знайдіть відмінності там, де посів уже загустився. Порівнюйте ділянки з тією самою культурою на схожій стадії росту. NDRE не має одиниць. Перш ніж вирішувати, чи потрібне добриво, огляньте рослини.',
  },
  reci: {
    en: 'Use unusual readings to pick spots to check in the field. RECI gives a clue about chlorophyll, the substance that makes leaves green. It is a unitless estimate.',
    uk: 'Виберіть місця для огляду, де показник відрізняється. RECI дає підказку про хлорофіл, речовину, що надає листю зеленого кольору. Показник не має одиниць.',
  },
  mcari: {
    en: 'Use differences in the reading from green leaves to choose where to check the crop. MCARI responds to chlorophyll. Bare soil and sparse plants can affect this unitless reading.',
    uk: 'За відмінностями в показнику виберіть місця для огляду посіву. MCARI реагує на хлорофіл у листі. Відкритий ґрунт і рідкі сходи можуть впливати на цей показник. Він не має одиниць.',
  },
  ndmi: {
    en: 'Find spots where plants may be drier and check them in the field. NDMI is unitless and does not measure soil moisture.',
    uk: 'Знайдіть місця, де рослини можуть бути сухішими, і перевірте їх у полі. NDMI не має одиниць і не вимірює вологість ґрунту.',
  },
  ndwi: {
    en: 'See where there may be open water on the satellite image, then check those areas in the field. NDWI is unitless and does not measure soil moisture or water depth.',
    uk: 'Знайдіть на знімку ділянки, де може стояти вода, і перевірте їх у полі. NDWI не має одиниць і не вимірює вологість ґрунту чи глибину води.',
  },
} satisfies Record<SpectralIndex, Record<HelpLanguage, string>>

export const fieldHelp = {
  en: {
    scene: {
      title: 'Satellite date and coverage',
      description: 'Before comparing dates, check how much of the field has usable data in each image. Coverage leaves out clouds, shadows and gaps. Zooming in or smoothing the map adds no new detail.',
    },
    variation: {
      title: 'Within-field variation',
      description: 'Use the colours to choose where to check the crop. They show where NDVI is lower, close to, or higher than the middle value for the field. The percentage covers only the area with usable data. Blank spots have no value shown.',
    },
    terrain: {
      title: 'Terrain directions',
      description: 'Follow the arrows to see which way the ground slopes. A 5% slope drops 5 m over 100 m horizontally. Height is shown in metres. Check small furrows and actual water flow in the field.',
    },
  },
  uk: {
    scene: {
      title: 'Дата знімка й покриття',
      description: 'Перед порівнянням дат подивіться, для якої частини поля є дані на кожному знімку. Хмари, тіні й прогалини не входять до покриття. Збільшення чи згладжування карти не додає деталей.',
    },
    variation: {
      title: 'Відмінності в межах поля',
      description: 'За кольорами виберіть місця для огляду посіву. Вони показують, де NDVI нижчий, близький або вищий за серединне значення для поля. Відсоток стосується лише ділянок із придатними даними. На прозорих ділянках значень не показано.',
    },
    terrain: {
      title: 'Напрямки схилів',
      description: 'Стрілки показують, у який бік іде схил. Нахил 5% означає спуск на 5 м за 100 м по горизонталі. Висоту показано в метрах. Дрібні борозни й те, куди насправді тече вода, перевірте в полі.',
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
    { label: uk ? 'Дата знімка' : 'Image date', value: date && Number.isFinite(Date.parse(date))
      ? new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(date)) : unknown },
    { label: uk ? 'Частина поля з даними' : 'Field area with usable data', value: coveragePercent != null && coveragePercent >= 0 && coveragePercent <= 100
      ? `${number(coveragePercent, 1, locale)}%` : unknown },
    { label: uk ? 'Розмір комірки розрахунку' : 'Analysis cell size', value: analysisResolutionMetres != null && Number.isFinite(analysisResolutionMetres) && analysisResolutionMetres > 0
      ? `${number(analysisResolutionMetres, 1, locale)} ${uk ? 'м' : 'm'}` : unknown },
  ]
}
