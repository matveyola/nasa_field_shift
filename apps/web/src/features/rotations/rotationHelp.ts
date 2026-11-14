type RotationHelpTopic = 'scenario' | 'yieldChange'
type HelpEntry = { title: string; description: string }

export const rotationHelp = {
  en: {
    scenario: {
      title: 'scenario',
      description: 'Choose a rainfall setting to compare your crop plans. The historical comparison uses past weather for all plans. A forecast uses the weather expected in the coming days, so the result can change.',
    },
    yieldChange: {
      title: 'yield change and prediction interval',
      description: 'See how the estimated yield compares with growing maize every year. A plus means more grain and a minus means less, in t/ha. The prediction interval shows how far results could vary. Without an estimate the difference is unknown.',
    },
  },
  uk: {
    scenario: {
      title: 'сценарій',
      description: 'Виберіть варіант опадів, щоб порівняти плани сівозміни. Для історичного порівняння беруть ту саму минулу погоду для всіх планів. Прогноз бере очікувану погоду на найближчі дні, тож результат може змінитися.',
    },
    yieldChange: {
      title: 'зміна врожайності та інтервал прогнозу',
      description: 'Порівняйте оцінку врожайності з варіантом, де кукурудза росте щороку. Плюс означає більше зерна, мінус — менше, у т/га. Інтервал прогнозу показує, наскільки результат може відрізнятися від оцінки. Якщо оцінки немає, різниця невідома.',
    },
  },
} satisfies Record<'en' | 'uk', Record<RotationHelpTopic, HelpEntry>>
