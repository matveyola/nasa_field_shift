type RotationHelpTopic = 'scenario' | 'yieldChange'
type HelpEntry = { title: string; description: string }

export const rotationHelp = {
  en: {
    scenario: {
      title: 'scenario',
      description: 'A scenario asks what would happen with the selected crop sequence and rainfall assumptions. A historical simulation uses past weather to compare plans under the same conditions. A forecast uses weather expected in the coming days. Both depend on the input data and model assumptions.',
    },
    yieldChange: {
      title: 'yield change and prediction interval',
      description: 'The model estimates how much more or less grain a crop sequence might yield than continuous maize, in t/ha. Use the prediction interval to see the uncertainty around that change. Missing estimates leave the change unknown.',
    },
  },
  uk: {
    scenario: {
      title: 'сценарій',
      description: 'Сценарій показує, що могло б статися за вибраної послідовності культур і припущень про опади. Історична симуляція порівнює плани в однакових умовах минулої погоди. Прогноз використовує очікувану погоду на найближчі дні. Обидва розрахунки залежать від вхідних даних і припущень моделі.',
    },
    yieldChange: {
      title: 'зміна врожайності та інтервал прогнозу',
      description: 'Модель оцінює, наскільки більше або менше зерна може дати послідовність культур порівняно з беззмінною кукурудзою, у т/га. Інтервал прогнозу показує невизначеність цієї зміни. Якщо оцінки бракує, зміна невідома.',
    },
  },
} satisfies Record<'en' | 'uk', Record<RotationHelpTopic, HelpEntry>>
