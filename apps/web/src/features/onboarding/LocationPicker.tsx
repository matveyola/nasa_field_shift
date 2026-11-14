import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { ChevronDownIcon } from '@heroicons/react/24/outline'
import { locationMatches } from './locations'

type LocationOption = { code: string; en: string; uk: string; local?: string }

type Props = {
  label: string
  placeholder: string
  noMatches: string
  language: 'en' | 'uk'
  options: LocationOption[]
  value: string
  disabled: boolean
  onSelect: (code: string) => void
}

export function LocationPicker({ label, placeholder, noMatches, language, options, value, disabled, onSelect }: Props) {
  const listId = useId()
  const input = useRef<HTMLInputElement>(null)
  const activeOption = useRef<HTMLLIElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState<string>()
  const [active, setActive] = useState(-1)
  const expanded = open && !disabled
  const labelValue = options.find(({ code }) => code === value)?.[language] ?? ''
  const matches = query === undefined ? options : options.filter((option) => locationMatches(option, query))
  const highlighted = matches[active]

  useEffect(() => {
    if (expanded && highlighted) activeOption.current?.scrollIntoView({ block: 'nearest' })
  }, [expanded, highlighted])

  function close() {
    setOpen(false)
    setQuery(undefined)
    setActive(-1)
  }

  function choose(code: string) {
    close()
    onSelect(code)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      setActive((index) => event.key === 'ArrowDown'
        ? Math.min(index + 1, matches.length - 1)
        : index < 0 ? matches.length - 1 : Math.max(index - 1, 0))
    } else if (event.key === 'Enter' && expanded && matches.length) {
      event.preventDefault()
      choose((highlighted ?? matches[0]).code)
    } else if (event.key === 'Escape' && expanded) {
      event.preventDefault()
      close()
    }
  }

  return <div className="setup-location-picker">
    <label htmlFor={listId + '-input'}>{label}</label>
    <div className="setup-location-input">
      <input ref={input} id={listId + '-input'} role="combobox" autoComplete="off" aria-autocomplete="list"
        aria-expanded={expanded} aria-controls={listId}
        aria-activedescendant={expanded && highlighted ? listId + '-' + active : undefined}
        placeholder={placeholder} value={query ?? labelValue} disabled={disabled}
        onFocus={() => { setOpen(true); input.current?.select() }}
        onClick={() => setOpen(true)} onBlur={close}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0) }}
        onKeyDown={onKeyDown} />
      <ChevronDownIcon aria-hidden="true" />
    </div>
    {expanded && <div className="setup-location-menu">
      <ul role="listbox" aria-label={label} id={listId}
        onMouseDown={(event) => event.preventDefault()}>
        {matches.map((option, index) => <li role="option" id={listId + '-' + index}
          key={option.code} ref={active === index ? activeOption : undefined}
          aria-selected={option.code === value}
          className={index === active ? 'is-highlighted' : undefined}
          onMouseMove={() => setActive(index)} onClick={() => choose(option.code)}>
          <span>{option[language]}</span>
        </li>)}
      </ul>
      {!matches.length && <p role="status">{noMatches}</p>}
    </div>}
  </div>
}
