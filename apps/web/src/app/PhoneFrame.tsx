import { useEffect, useState } from 'react'
import './phoneFrame.css'

// Field Shift is a phone app. On a wide screen it is shown in an iPhone (393 × 852 points, the iPhone 15 Pro), with
// its status bar and home indicator; `?desktop` opens the wide layout instead. A real phone never sees the frame.
const SCREEN = { width: 393, height: 852 }
const BEZEL = 12

export function framedPreview(): boolean {
  // `?desktop` is remembered for the tab: the app's own links drop unknown query parameters.
  try { if (new URLSearchParams(location.search).has('desktop')) sessionStorage.setItem('field-shift-desktop', '1') } catch { /* private mode */ }
  let desktop = false
  try { desktop = sessionStorage.getItem('field-shift-desktop') === '1' } catch { /* private mode */ }
  return window.self === window.top && matchMedia('(min-width: 700px)').matches && !desktop
}

/** Inside the preview's iframe: the frame draws the status bar, the app keeps clear of the home indicator. */
export function insidePhoneFrame(): boolean {
  return window.self !== window.top
}

function clock() {
  return new Date().toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' })
}

export function PhoneFrame() {
  const [time, setTime] = useState(clock)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const tick = setInterval(() => setTime(clock()), 15_000)
    const fit = () => setScale(Math.min(1, (innerHeight - 32) / (SCREEN.height + 2 * BEZEL), (innerWidth - 32) / (SCREEN.width + 2 * BEZEL)))
    fit()
    addEventListener('resize', fit)
    return () => { clearInterval(tick); removeEventListener('resize', fit) }
  }, [])
  const src = location.pathname + location.search + location.hash

  return (
    <div className="phone-stage">
      <div className="phone" style={{ transform: `scale(${scale})` }}>
        <span className="phone-button phone-button--action" aria-hidden="true" />
        <span className="phone-button phone-button--up" aria-hidden="true" />
        <span className="phone-button phone-button--down" aria-hidden="true" />
        <span className="phone-button phone-button--side" aria-hidden="true" />
        <div className="phone-screen">
          <div className="phone-status" aria-hidden="true">
            <span className="phone-time">{time}</span>
            <span className="phone-island" />
            <span className="phone-icons">
              <svg viewBox="0 0 18 12" width="18" height="12"><g fill="currentColor">
                <rect x="0" y="8" width="3" height="4" rx="1" /><rect x="5" y="5.5" width="3" height="6.5" rx="1" />
                <rect x="10" y="3" width="3" height="9" rx="1" /><rect x="15" y="0" width="3" height="12" rx="1" />
              </g></svg>
              <svg viewBox="0 0 16 12" width="16" height="12"><path fill="currentColor" d="M8 2.4c2.3 0 4.4.9 6 2.4l1.3-1.4A10.6 10.6 0 0 0 8 .5 10.6 10.6 0 0 0 .7 3.4L2 4.8a8.6 8.6 0 0 1 6-2.4Zm0 3.8c1.3 0 2.5.5 3.4 1.3l1.3-1.4A6.8 6.8 0 0 0 8 4.3a6.8 6.8 0 0 0-4.7 1.8l1.3 1.4C5.5 6.7 6.7 6.2 8 6.2Zm0 3.7c.4 0 .8.2 1.1.4L8 11.5l-1.1-1.2c.3-.2.7-.4 1.1-.4Z" /></svg>
              <svg viewBox="0 0 27 13" width="27" height="13"><rect x=".5" y=".5" width="23" height="12" rx="3.8" fill="none" stroke="currentColor" opacity=".38" /><rect x="2" y="2" width="18" height="9" rx="2.4" fill="currentColor" /><path d="M25 4.5v4c.8-.3 1.4-1.1 1.4-2s-.6-1.7-1.4-2Z" fill="currentColor" opacity=".4" /></svg>
            </span>
          </div>
          <iframe className="phone-app" title="Field Shift" src={src} />
          <span className="phone-home" aria-hidden="true" />
        </div>
      </div>
      <a className="phone-desktop-link" href="/?desktop">Широкий екран</a>
    </div>
  )
}
