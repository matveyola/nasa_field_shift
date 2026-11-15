/** Summarize only returned readings; missing hours never become zero or a full-day claim. */
export function temperatureRange(hours: { temperature_c: number | null }[]) {
  const values = hours.map(hour => hour.temperature_c).filter((value): value is number => value !== null && Number.isFinite(value))
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : null
}
