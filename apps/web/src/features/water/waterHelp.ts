type Language = 'en' | 'uk'
type WaterHelpTopic = 'surfaceMoisture' | 'rootZoneReserve' | 'stressDays' | 'initialReserve' | 'irrigation'
type HelpEntry = { title: string; description: string }

export const waterHelp = {
  en: {
    surfaceMoisture: {
      title: 'surface soil moisture',
      description: 'Watch this number to see if the top layer is drying. A reading of 0.25 m³/m³ means water fills 25% of that layer’s volume. Check how much water roots can use in the root-zone reserve.',
    },
    rootZoneReserve: {
      title: 'root-zone water reserve',
      description: 'See how much water roots may still be able to use. The reserve is in mm of water, like rainfall. Below the stress line on the chart, the crop may run short. The estimate uses a standard soil and an estimated root depth.',
    },
    stressDays: {
      title: 'water stress days',
      description: 'Compare plans over the same period. Fewer stress days means less time at risk of water shortage. The model counts days when the reserve for roots drops below its stress threshold.',
    },
    initialReserve: {
      title: 'initial water reserve',
      description: 'See how much water roots could use at the start of the calculation, in mm. A higher starting reserve can delay a shortage. This estimate comes from the forecast’s soil moisture data.',
    },
    irrigation: {
      title: 'irrigation',
      description: 'This label tells you whether you can water the field. In a scenario, the amount of water you add is shown in mm. Adding 1 mm means applying 1 litre per square metre.',
    },
  },
  uk: {
    surfaceMoisture: {
      title: 'вологість поверхневого шару ґрунту',
      description: 'Стежте за цим числом, щоб помітити, коли підсихає верхній шар ґрунту. Значення 0,25 м³/м³ означає, що вода займає 25% об’єму цього шару. Скільки води може взяти коріння, показує окремий показник запасу води.',
    },
    rootZoneReserve: {
      title: 'запас води в кореневій зоні',
      description: 'Подивіться, скільки води ще може взяти коріння. Запас показано в мм шару води, як і опади. Якщо запас нижче лінії порога на графіку, культурі може бракувати води. Розрахунок використовує типові властивості ґрунту й оцінку глибини коріння.',
    },
    stressDays: {
      title: 'дні водного стресу',
      description: 'Порівнюйте плани за однаковий період. Що менше таких днів, то менше часу культурі може бракувати води. Модель рахує дні, коли запас води для коріння нижчий за поріг.',
    },
    initialReserve: {
      title: 'початковий запас води',
      description: 'Це оцінка того, скільки води коріння могло взяти на початку розрахунку, у мм. Що більший запас на старті, то пізніше може настати нестача. Оцінка спирається на дані прогнозу про вологу в ґрунті.',
    },
    irrigation: {
      title: 'зрошення',
      description: 'Позначка показує, чи можна поливати поле. У сценарії кількість доданої води показують у мм. Додати 1 мм води означає подати 1 літр на квадратний метр.',
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
