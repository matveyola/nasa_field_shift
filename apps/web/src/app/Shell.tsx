import { LanguageSelect, useLocale } from '../shared/locale'
import type { MouseEvent, ReactNode } from 'react'
import {
  ArrowTrendingUpIcon, ChartBarSquareIcon, BuildingOffice2Icon, HomeModernIcon, MapIcon, SparklesIcon, UserIcon,
} from '@heroicons/react/24/outline'
import { pages, routeAddress, type Page, type Route } from './routing'
import { HeaderActions } from '../shared/HeaderActions'
import { FieldMark } from '@field-shift/agent-widget'

const destinations = {
  fields: { label: 'Fields', icon: MapIcon },
  strategies: { label: 'Strategies', icon: ArrowTrendingUpIcon },
  agent: { label: 'Assistant', icon: SparklesIcon },
  analytics: { label: 'Analytics', icon: ChartBarSquareIcon },
  farm: { label: 'Farm', icon: HomeModernIcon },
}

export function Shell({
  route, farmName, navigate, assistantOpen, openAssistant, children,
}: { route: Route; farmName?: string; navigate: (page: Page) => void; assistantOpen: boolean; openAssistant: () => void; children: ReactNode }) {
  const { t } = useLocale()
  function follow(event: MouseEvent<HTMLAnchorElement>, page: Page) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    event.preventDefault()
    navigate(page)
  }

  return (
    <div className={'app-shell page-' + route.page}>
      <a className="skip-link" href="#content">{t('Skip to content')}</a>
      <header className="mobile-header">
        <a className="mobile-brand" href={routeAddress({ ...route, page: 'fields' })} onClick={(event) => follow(event, 'fields')}>
          <BrandLogo />
          <span>Field Shift<small>AGRITECH</small></span>
        </a>
        <div className="mobile-header-actions">
          <LanguageSelect />
          <HeaderActions />
          <a className="mobile-farm" aria-label={t('Your farm')} href={routeAddress({ ...route, page: 'farm' })}
            onClick={(event) => follow(event, 'farm')}><UserIcon aria-hidden="true" /></a>
        </div>
      </header>
      <aside className="sidebar">
        <a className="brand" href={routeAddress({ ...route, page: 'fields' })} onClick={(event) => follow(event, 'fields')}>
          <BrandLogo />
          <span>Field Shift</span>
          <small>AGRITECH</small>
        </a>
        <nav aria-label={t('Main navigation')} className="main-nav">
          {pages.map((page) => {
            // «Польовий помічник»: its round mark in Agent's place (the middle of the phone's bar) opens it.
            if (page === 'agent') return (
              <button key={page} type="button" className="nav-link nav-assistant" aria-haspopup="dialog" aria-expanded={assistantOpen}
                onClick={openAssistant}>
                <span className="nav-assistant-mark" aria-hidden="true"><FieldMark /></span>
                <span>{t('Agent')}</span>
              </button>
            )
            const { label, icon: Icon } = destinations[page]
            return (
              <a key={page} className={'nav-link nav-' + page} aria-current={route.page === page ? 'page' : undefined}
                href={routeAddress({ ...route, page })}
                onClick={(event) => follow(event, page)}>
                <Icon aria-hidden="true" />
                {page === 'farm' ? <><span className="nav-label-desktop">{t('Farm management')}</span><span className="nav-label-mobile">{t(label)}</span></> : <span>{t(label)}</span>}
              </a>
            )
          })}
        </nav>
        <a className="farm-link" href={routeAddress({ ...route, page: 'farm' })} onClick={(event) => follow(event, 'farm')}>
          <BuildingOffice2Icon aria-hidden="true" />
          <span>{farmName ?? t('Your farm')}</span>
        </a>
      </aside>
      <main id="content" className="workspace" tabIndex={-1}>{children}</main>
    </div>
  )
}

// The Field Shift leaf, white on the black disc of «Польовий помічник»'s mark.
function BrandLogo() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 44 38" fill="none">
        <path d="M5 31C12 12 23 11 39 5c-2 15-11 26-26 27l-8-1Z" fill="currentColor" />
        <path d="M3 35C11 24 21 17 37 7" stroke="#0d0d0d" strokeWidth="2.3" strokeLinecap="round" />
      </svg>
    </span>
  )
}
