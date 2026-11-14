import { LanguageSelect, useLocale } from '../shared/locale'
import type { MouseEvent, ReactNode } from 'react'
import {
  ArrowPathIcon, BeakerIcon, BuildingOffice2Icon, HomeModernIcon, MapIcon, SparklesIcon, UserIcon,
} from '@heroicons/react/24/outline'
import { pages, routeAddress, type Page, type Route } from './routing'
import { HeaderActions } from '../shared/HeaderActions'
import { FieldMark } from '@field-shift/agent-widget'

const destinations = {
  fields: { label: 'Fields', icon: MapIcon },
  water: { label: 'Water', icon: BeakerIcon },
  agent: { label: 'Assistant', icon: SparklesIcon },
  rotations: { label: 'Rotations', icon: ArrowPathIcon },
  farm: { label: 'Farm', icon: HomeModernIcon },
}

export function Shell({
  route, homeAddress, farmName, navigate, navigateHome, assistantOpen, openAssistant, children,
}: {
  route: Route; homeAddress: string; farmName?: string; navigate: (page: Page) => void; navigateHome: () => void
  assistantOpen: boolean; openAssistant: () => void; children: ReactNode
}) {
  const { t } = useLocale()
  function follow(event: MouseEvent<HTMLAnchorElement>, visit: () => void) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    event.preventDefault()
    visit()
  }
  const tabAddress = (page: Page) => routeAddress({ ...route, page, home: undefined, setupId: undefined })

  return (
    <div className={'app-shell page-' + route.page}>
      <a className="skip-link" href="#content">{t('Skip to content')}</a>
      <header className="mobile-header">
        <a className="mobile-brand" href={homeAddress} onClick={(event) => follow(event, navigateHome)}>
          <BrandLogo />
          <span>Field Shift<small>AGRITECH</small></span>
        </a>
        <div className="mobile-header-actions">
          <LanguageSelect />
          <HeaderActions />
          <a className="mobile-farm" aria-label={t('Your farm')} href={tabAddress('farm')}
            onClick={(event) => follow(event, () => navigate('farm'))}><UserIcon aria-hidden="true" /></a>
        </div>
      </header>
      <aside className="sidebar">
        <a className="brand" href={homeAddress} onClick={(event) => follow(event, navigateHome)}>
          <BrandLogo />
          <span>Field Shift</span>
          <small>AGRITECH</small>
        </a>
        <nav aria-label={t('Main navigation')} className="main-nav">
          {pages.map((page) => {
            // «Польовий помічник»: its round mark in Agent's place (the middle of the phone's bar) opens it.
            if (page === 'agent') return (
              <button key={page} type="button" className="nav-link nav-assistant" aria-haspopup="dialog" aria-expanded={assistantOpen}
                disabled={Boolean(route.setupId)}
                onClick={openAssistant}>
                <span className="nav-assistant-mark" aria-hidden="true"><FieldMark /></span>
                <span>{t('Assistant')}</span>
              </button>
            )
            const { label, icon: Icon } = destinations[page]
            return (
              <a key={page} className={'nav-link nav-' + page} aria-current={route.page === page ? 'page' : undefined}
                href={tabAddress(page)}
                onClick={(event) => follow(event, () => navigate(page))}>
                <Icon aria-hidden="true" />
                {page === 'farm' ? <><span className="nav-label-desktop">{t('Farm management')}</span><span className="nav-label-mobile">{t(label)}</span></> : <span>{t(label)}</span>}
              </a>
            )
          })}
        </nav>
        <a className="farm-link" href={tabAddress('farm')} onClick={(event) => follow(event, () => navigate('farm'))}>
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
