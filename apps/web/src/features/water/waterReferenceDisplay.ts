import type { WaterForecastApplicability } from '../../api/contracts'

/** Interpretation only: never changes the numeric outlook or qualifies crop suitability. */
export function waterReferenceDisplay(applicability: WaterForecastApplicability | null | undefined, shortage: boolean, language: string) {
  const label = (en: string, uk: string) => language === 'uk' ? uk : en
  const review = applicability?.status === 'review_required'
  return {
    tone: review ? 'review' : shortage ? 'shortage' : 'reference',
    summary: shortage
      ? label('Modeled reserve below reference threshold', 'Розрахунковий запас нижче довідкового порога')
      : label('Modeled reserve at or above reference threshold', 'Розрахунковий запас не нижче довідкового порога'),
    compact: review
      ? label('Shallow 0–7 cm forecast ≤0 °C; freezing is not modeled.', 'Прогноз для шару 0–7 см ≤0 °C; замерзання не моделюється.')
      : !applicability
        ? label('Temperature applicability unknown; reference model only.', 'Температурна застосовність невідома; лише довідкова модель.')
        : label('Crop suitability and liquid water are not assessed.', 'Придатність для культури та рідку воду не оцінено.'),
    caveat: review
      ? label('Review required: the grid forecast has daily mean soil temperature at or below 0 °C in the shallow 0–7 cm layer. This does not establish freezing throughout the root zone.', 'Потрібна перевірка: прогноз сітки має середньодобову температуру ґрунту 0 °C або нижче у поверхневому шарі 0–7 см. Це не доводить промерзання всієї кореневої зони.')
      : !applicability
        ? label('Temperature applicability is unknown for this saved run; no shallow-soil diagnostic was stored.', 'Температурна застосовність цього збереженого розрахунку невідома: діагностику поверхневого ґрунту не збережено.')
        : label('Above-zero shallow soil temperatures do not establish crop suitability or liquid water throughout the root zone.', 'Додатна температура поверхневого ґрунту не доводить придатності для культури чи наявності рідкої води в усій кореневій зоні.'),
    limitation: label('This reference bucket does not model freezing, thawing or dormancy. Its reserve and threshold are not a measurement of liquid plant-available water or a crop-growth assessment.', 'Ця довідкова балансова модель не враховує замерзання, відтавання чи стан спокою. Її запас і поріг не є вимірюванням доступної рослинам рідкої води або оцінкою росту культури.'),
  }
}
