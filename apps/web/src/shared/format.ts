export function number(value: number, digits = 1, locale = 'en', signed = false): string {
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: digits, signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(value)
}

export function shortDate(value: string, locale = 'en'): string {
  return new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', timeZone: 'UTC' })
    .format(new Date(value))
}
