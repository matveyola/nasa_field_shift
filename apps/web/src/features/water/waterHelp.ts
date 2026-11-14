type Language = 'en' | 'uk'
type WaterHelpTopic = 'surfaceMoisture' | 'rootZoneReserve' | 'stressDays' | 'initialReserve' | 'irrigation'
type HelpEntry = { title: string; description: string }

export const waterHelp = {
  en: {
    surfaceMoisture: {
      title: 'surface soil moisture',
      description: 'Water per volume of soil near the surface, in m³/m³. Use it to see whether the top layer is drying. It does not tell you how much water is available to roots.',
    },
    rootZoneReserve: {
      title: 'root-zone water reserve',
      description: 'The model estimates how much water the crop can draw from its root zone, in mm of water spread over the field. Compare it with the stress threshold to see when the crop may run short. This estimate uses a reference soil and a modelled root depth.',
    },
    stressDays: {
      title: 'water stress days',
      description: 'The number of days when the modelled root-zone reserve falls below its stress threshold. Compare plans over the same period to see which one spends longer under water stress. The threshold depends on the model inputs.',
    },
    initialReserve: {
      title: 'initial water reserve',
      description: 'The estimated water available to roots at the start of the calculation, in mm. Starting with more water can delay a modelled shortage. The forecast starts from the source model’s soil layers at initialization.',
    },
    irrigation: {
      title: 'irrigation',
      description: 'Availability tells you whether the field can be irrigated. A scenario can specify how much water is applied in mm. One mm is one litre per square metre.',
    },
  },
  uk: {
    surfaceMoisture: {
      title: 'вологість поверхневого шару ґрунту',
      description: 'Об’єм води на одиницю об’єму ґрунту біля поверхні, у м³/м³. Показник допомагає помітити, що верхній шар підсихає. За ним не можна визначити, скільки води доступно корінню.',
    },
    rootZoneReserve: {
      title: 'запас води в кореневій зоні',
      description: 'Модель оцінює, скільки води культура може отримати з кореневої зони, у мм шару води по площі поля. Порівняйте запас із порогом стресу, щоб помітити ризик нестачі. Оцінка спирається на еталонний ґрунт і розрахункову глибину коріння.',
    },
    stressDays: {
      title: 'дні водного стресу',
      description: 'Кількість днів, коли розрахунковий запас води в кореневій зоні нижчий за поріг стресу. Порівняйте плани за однаковий період, щоб побачити, за якого культура довше відчувала б нестачу води. Поріг залежить від вхідних даних моделі.',
    },
    initialReserve: {
      title: 'початковий запас води',
      description: 'Оцінка води, доступної корінню на початку розрахунку, у мм. Вищий початковий запас може відтермінувати розрахункову нестачу води. Прогноз бере початкові дані про шари ґрунту з моделі джерела.',
    },
    irrigation: {
      title: 'зрошення',
      description: 'Доступність показує, чи можна зрошувати поле. У сценарії кількість поданої води можна задати в мм. Один мм дорівнює одному літру на квадратний метр.',
    },
  },
} satisfies Record<Language, Record<WaterHelpTopic, HelpEntry>>

// Values come from the active result, formatted in the selected locale.
export function waterHelpMetadata(language: Language, { depth, source }: { depth?: string; source?: string }) {
  if (language === 'uk') {
    return `Глибина: ${depth?.trim() || 'невідома'}. Джерело даних: ${source?.trim() || 'невідоме'}.`
  }
  return `Depth: ${depth?.trim() || 'unknown'}. Data source: ${source?.trim() || 'unknown'}.`
}
