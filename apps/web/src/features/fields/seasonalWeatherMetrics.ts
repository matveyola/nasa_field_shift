import type { SeasonalWeatherDay } from '../../api/weather'

export function seasonalWeatherMetrics(days: SeasonalWeatherDay[], base: number) {
  let precipitation = 0
  let degreeDays = 0
  let precipitationDays = 0
  let temperatureDays = 0
  const points = days.map(({ date, precipitation_mm, temperature_min_c, temperature_max_c }) => {
    const dailyDegreeDays = temperature_min_c === null || temperature_max_c === null
      ? null : Math.max(0, (temperature_min_c + temperature_max_c) / 2 - base)
    if (precipitation_mm !== null) {
      precipitation += precipitation_mm
      precipitationDays++
    }
    if (dailyDegreeDays !== null) {
      degreeDays += dailyDegreeDays
      temperatureDays++
    }
    return {
      date,
      dailyDegreeDays,
      cumulativePrecipitation: precipitation_mm === null ? null : precipitation,
      cumulativeDegreeDays: dailyDegreeDays === null ? null : degreeDays,
    }
  })
  return {
    points, precipitationDays, temperatureDays,
    precipitation: precipitationDays ? precipitation : null,
    degreeDays: temperatureDays ? degreeDays : null,
  }
}
