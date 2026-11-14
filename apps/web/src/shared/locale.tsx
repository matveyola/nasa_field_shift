import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { number as formatNumber, shortDate as formatShortDate } from './format'
import { ukrainian } from './ukrainian'

type Language = 'en' | 'uk'
const storageKey = 'field-shift-language'
const Locale = createContext<Language>('en')
const UpdateLocale = createContext<(language: Language) => void>(() => {})

const cropInputs = new Map([
  ['кукурудза', 'Maize'], ['соя', 'Soy'], ['пшениця', 'Wheat'], ['яра пшениця', 'Spring wheat'],
])

export function cropInput(value: string) {
  return cropInputs.get(value.toLocaleLowerCase('uk-UA')) ?? value
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(() => {
    try { return localStorage.getItem(storageKey) === 'uk' ? 'uk' : 'en' }
    catch { return 'en' }
  })

  useEffect(() => {
    document.documentElement.lang = language
    try { localStorage.setItem(storageKey, language) } catch { /* The session preference still works. */ }
  }, [language])

  return <Locale value={language}><UpdateLocale value={setLanguage}>{children}</UpdateLocale></Locale>
}

export function useLocale() {
  const language = useContext(Locale)
  return useMemo(() => {
    const tag = language === 'uk' ? 'uk-UA' : 'en'
    const t = (text: string) => language === 'uk' && Object.hasOwn(ukrainian, text) ? ukrainian[text] : text
    const number = (value: number, digits = 1, signed = false) =>
      formatNumber(value, digits, tag, signed)
    const date = (value: string, withTime = false) => new Intl.DateTimeFormat(tag, {
      dateStyle: 'medium', ...(withTime ? { timeStyle: 'short' } : {}), timeZone: 'UTC',
    }).format(new Date(value))
    const shortDate = (value: string) => formatShortDate(value, tag)
    const crop = (name: string) => {
      if (language === 'en') return name
      const key = name.toLowerCase()
      return Object.hasOwn(ukrainian, key) ? ukrainian[key] : name
    }
    return { language, t, number, date, shortDate, crop }
  }, [language])
}

export function LanguageSelect() {
  const { language, t } = useLocale()
  const update = useContext(UpdateLocale)
  return (
    <select className="language-select" aria-label={t('Language')} value={language}
      onChange={(event) => update(event.target.value === 'uk' ? 'uk' : 'en')}>
      <option value="en" lang="en">English</option>
      <option value="uk" lang="uk">Українська</option>
    </select>
  )
}
