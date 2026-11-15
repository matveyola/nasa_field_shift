import { InformationCircleIcon, ChevronLeftIcon, ChevronRightIcon, ExclamationTriangleIcon, SparklesIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { api } from '../../api/client'
import type { Field } from '../../api/contracts'
import type { AnalyticsSection } from '../../shared/productContext'
import { CropIcon } from '../../shared/CropIcon'
import { useLocale } from '../../shared/locale'
import { useResource } from '../../shared/useResource'
import { FieldWaterAssistant } from '../agent/FieldWaterAssistant'
import { waterReferenceDisplay } from '../water/waterReferenceDisplay'
import { getCurrentWaterOutlook } from '../water/waterOutlook'
import type { VegetationResult } from '../../api/analytics'
import { FieldVegetationSignal } from '../agent/FieldVegetationSignal'
import './fieldAnalytics.css' // the Overview / Field analysis switch lives here, before the analysis panel loads

type Props = {
  field: Field
  season?: number
  openStrategies?: () => void
  openAnalytics?: (section?: AnalyticsSection) => void
  openAgent?: () => void
  canSwitch: boolean
  onStep: (direction: -1 | 1) => void
  surface?: { value: number; date: string; provider: string }
  openForecast: (fieldId: string, day?: string) => void
  surfaceForecastOpen: boolean
  onSheetPosition: (fieldId: string, top?: number) => void
  children: ReactNode
  analysis: (inspectMap: () => void) => ReactNode
  vegetation?: VegetationResult
  initialAnalysis?: boolean
  mapFirst?: boolean
}

export function FieldContextPanel({ field, season, openStrategies, openAnalytics, openAgent, canSwitch, onStep, surface, openForecast, surfaceForecastOpen, onSheetPosition, children, analysis, vegetation, initialAnalysis, mapFirst }: Props) {
  const { t, number, crop } = useLocale()
  return <aside id="field-context-panel" className="field-context-panel" aria-label={t('Selected field')}>
      <header className="field-context-heading">
        <p className="field-context-overline">{t('Selected field')}</p>
        <div className="field-context-switcher">
          <button type="button" className="icon-button" aria-label={t('Previous field')} disabled={!canSwitch} onClick={() => onStep(-1)}>
            <ChevronLeftIcon aria-hidden="true" />
          </button>
          <h2 title={field.name}>{field.name}</h2>
          <button type="button" className="icon-button" aria-label={t('Next field')} disabled={!canSwitch} onClick={() => onStep(1)}>
            <ChevronRightIcon aria-hidden="true" />
          </button>
        </div>
        <p className="field-context-crop"><CropIcon crop={field.crop} /><span>{field.crop.trim() ? crop(field.crop) : t('Crop unknown')} · {number(field.area_ha)} {t('ha')}</span></p>
        {field.boundary_origin === 'example' && <small className="chip">{t('Example field')}</small>}
      </header>
    <FieldContextSheet key={`${field.id}:${field.boundary_revision}`} field={field} surface={surface}
      season={season} openStrategies={openStrategies} openAnalytics={openAnalytics} openAgent={openAgent}
      openForecast={openForecast} surfaceForecastOpen={surfaceForecastOpen} onSheetPosition={onSheetPosition}
      analysis={analysis} vegetation={vegetation} initialAnalysis={initialAnalysis} mapFirst={mapFirst}>
      {children}
    </FieldContextSheet>
  </aside>
}

function FieldContextSheet({ field, season, openStrategies, openAnalytics, openAgent, surface, openForecast, surfaceForecastOpen, onSheetPosition, children, analysis, vegetation, initialAnalysis, mapFirst }: Omit<Props, 'canSwitch' | 'onStep'>) {
  const { t, number, shortDate, language } = useLocale()
  const load = useCallback((signal: AbortSignal) => api.waterForecast(field.id, signal), [field.id])
  const saved = useResource(load, 5 * 60 * 1000)
  const outlook = getCurrentWaterOutlook(saved.data, field.id, new Date().toISOString().slice(0, 10), season)
  const [expanded, setExpanded] = useState(Boolean(initialAnalysis && !mapFirst))
  const [view, setView] = useState<'overview' | 'analysis'>(initialAnalysis ? 'analysis' : 'overview')
  const [analysisOpened, setAnalysisOpened] = useState(Boolean(initialAnalysis))
  const [mobile, setMobile] = useState(() => matchMedia('(max-width: 1000px)').matches)
  const trigger = useRef<HTMLButtonElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const wasExpanded = useRef(false)
  const restoreFocus = useRef(true)
  const sheetId = useId()
  const sheet = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setView(initialAnalysis ? 'analysis' : 'overview')
    if (initialAnalysis) setExpanded(!mapFirst)
  }, [initialAnalysis, mapFirst])

  useEffect(() => {
    if (view === 'analysis') setAnalysisOpened(true)
  }, [view])

  useEffect(() => {
    if (!mobile || !expanded || !sheet.current) return
    const measure = () => onSheetPosition(field.id, sheet.current?.getBoundingClientRect().top)
    const observer = new ResizeObserver(measure)
    observer.observe(sheet.current)
    window.addEventListener('resize', measure)
    measure()
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
      onSheetPosition(field.id)
    }
  }, [mobile, expanded, field.id, onSheetPosition])

  useEffect(() => {
    const query = matchMedia('(max-width: 1000px)')
    const update = () => setMobile(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (expanded && mobile) close.current?.focus({ preventScroll: true })
    else if (wasExpanded.current && mobile && restoreFocus.current) trigger.current?.focus({ preventScroll: true })
    wasExpanded.current = expanded
  }, [expanded, mobile])

  useEffect(() => {
    if (surfaceForecastOpen) {
      restoreFocus.current = false
      setExpanded(false)
    }
  }, [surfaceForecastOpen])

  function closeSheet() {
    restoreFocus.current = true
    setExpanded(false)
  }

  const risk = outlook.kind === 'shortage'
  const display = waterReferenceDisplay(saved.data?.applicability, risk, language)
  const summary = outlook.kind === 'unavailable' ? t('Plan your next sowing') : display.summary

  return (
      <div ref={sheet} className={'field-context-sheet' + (expanded ? ' is-expanded' : '')}
        onKeyDown={(event) => { if (event.key === 'Escape' && expanded && mobile) { event.stopPropagation(); closeSheet() } }}>
        <span className="field-sheet-handle" aria-hidden="true" />
        <button type="button" ref={trigger} className={'field-sheet-trigger is-' + (outlook.kind === 'unavailable' ? outlook.kind : display.tone)}
          aria-expanded={expanded} aria-controls={sheetId} onClick={() => { restoreFocus.current = true; setExpanded(true) }}>
          {outlook.kind === 'unavailable' ? <SparklesIcon aria-hidden="true" /> : display.tone === 'shortage' ? <ExclamationTriangleIcon aria-hidden="true" /> : <InformationCircleIcon aria-hidden="true" />}
          <span><strong>{summary}</strong><small>{outlook.kind === 'unavailable' ? field.name : display.compact}</small>{risk && <small>{t('From')} {shortDate(outlook.firstShortage.date)}</small>}</span>
          <ChevronRightIcon aria-hidden="true" />
        </button>
        <div id={sheetId} className="field-sheet-body" inert={mobile && !expanded}>
          <button ref={close} type="button" className="icon-button field-sheet-close" aria-label={t('Close field assistant')} onClick={closeSheet}>
            <XMarkIcon aria-hidden="true" />
          </button>
          <div className="field-context-view" role="group" aria-label={t('Field panel view')}>
            <button type="button" aria-pressed={view === 'overview'} onClick={() => setView('overview')}>{t('Overview')}</button>
            <button type="button" aria-pressed={view === 'analysis'} onClick={() => setView('analysis')}>{t('Map observations')}</button>
          </div>
          <div hidden={view !== 'overview'}>
          <section className="field-decision">
            <p className="field-context-overline">{t('Next decision')}</p>
            <h3>{t('What should grow here next?')}</h3>
            <p>{t('Compare upcoming planting opportunities, check the reasons and prepare your field plan.')}</p>
            <div className="field-decision-actions">
              {openStrategies && <button type="button" className="button" onClick={openStrategies}>{t('Plan upcoming sowing')}</button>}
              {openAgent && <button type="button" className="button subtle" onClick={openAgent}>{t('Discuss this field')}</button>}
            </div>
          </section>
          <FieldWaterAssistant field={field} forecast={saved} outlook={outlook} openForecast={openForecast} />
          <FieldVegetationSignal data={vegetation} openAnalysis={() => openAnalytics ? openAnalytics('vegetation') : setView('analysis')} />
          {openAnalytics && <button className="button subtle" type="button" onClick={() => openAnalytics('overview')}>{t('Explore field evidence')}</button>}
          <details className="field-context-details">
          <summary>{t('Measurements and assumptions')}</summary>
          <div className="field-context-overview">
            <dl>
              <div><dt>{t('Surface moisture')}</dt><dd>{surface ? <>{number(surface.value, 3)} <small>{t('m³/m³')}</small></> : t('Unavailable')}</dd></div>
              <div><dt>{t('Root-zone reserve')}</dt><dd>{outlook.kind !== 'unavailable' ? <>{number(outlook.firstDay.reserve_mm)} <small>{t('mm')}</small></> : t('Unavailable')}</dd></div>
            </dl>
            {surface && <p>{t('Surface moisture')} · <time dateTime={surface.date}>{shortDate(surface.date)}</time> · {surface.provider}</p>}
            {outlook.kind !== 'unavailable' && <p>{t('Root-zone reserve')} · <time dateTime={outlook.firstDay.date}>{shortDate(outlook.firstDay.date)}</time> · FAO-56</p>}
          </div>
          </details>
          <div className="field-context-tools">{children}</div>
          </div>
          {(analysisOpened || view === 'analysis') && (
            <div hidden={view !== 'analysis'}>{analysis(() => { if (mobile) closeSheet() })}</div>
          )}
        </div>
      </div>
  )
}
