import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { accessApi, type AccessSession } from '../../api/access'
import { setAccessCsrf } from '../../api/client'
import { LanguageSelect, useLocale } from '../../shared/locale'
import { ErrorBox, Loading } from '../../shared/ui'
import './access.css'

type AccessValue = { session: AccessSession | null; canEdit: boolean; isAdmin: boolean; refresh: () => Promise<void> }
const Access = createContext<AccessValue>({ session: null, canEdit: true, isAdmin: false, refresh: async () => {} })
export const useAccess = () => useContext(Access)

export function AccessProvider({ children }: { children: ReactNode }) {
  const { t } = useLocale()
  const [session, setSession] = useState<AccessSession | null>(null)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const refresh = useCallback(async () => {
    const current = ++generation.current
    try {
      const value = await accessApi.session()
      if (current !== generation.current) return
      setAccessCsrf(value.csrf_token)
      setSession(value)
      setError('')
    } catch (cause) { if (current === generation.current) setError(cause instanceof Error ? cause.message : 'Unable to check access.') }
  }, [])
  useEffect(() => {
    void refresh()
    const expired = () => { setAccessCsrf(null); setSession(null); void refresh() }
    window.addEventListener('field-shift-session-expired', expired)
    return () => { generation.current += 1; window.removeEventListener('field-shift-session-expired', expired) }
  }, [refresh])
  if (error) return <main className="access-gate"><ErrorBox message={error} retry={() => void refresh()} /></main>
  if (!session) return <main className="access-gate"><Loading text={t('Checking workspace access…')} /></main>
  if (!session.authenticated) return <AccessForm session={session} done={refresh} />
  return <Access value={{ session, canEdit: session.role !== 'viewer', isAdmin: session.role === 'admin', refresh }}>
    {session.role === 'viewer' && <div className="access-viewer-banner" role="status">{t('View-only access. Ask an administrator to edit farm data.')}</div>}
    {children}
  </Access>
}

function AccessForm({ session, done }: { session: AccessSession; done: () => Promise<void> }) {
  const { t } = useLocale()
  const [token, setToken] = useState(() => new URLSearchParams(location.hash.slice(1)).get('invite') ?? '')
  useEffect(() => { if (new URLSearchParams(location.hash.slice(1)).has('invite')) history.replaceState(null, '', location.pathname + location.search) }, [])
  const [mode, setMode] = useState<'login' | 'setup' | 'accept'>(token ? 'accept' : session.needs_setup ? 'setup' : 'login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError('')
    try {
      if (mode === 'login') await accessApi.login({ email, password })
      else await accessApi[mode]({ email, password, name, token })
      setPassword(''); setToken(''); await done()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to sign in.') }
    finally { setPending(false) }
  }
  return <main className="access-gate"><section className="card access-card">
    <header><strong>Field Shift</strong><LanguageSelect /></header>
    <h1>{t(mode === 'login' ? 'Sign in to your farm' : mode === 'setup' ? 'Set up your farm team' : 'Accept an invitation')}</h1>
    <p>{t(mode === 'setup' ? 'Create the first administrator with the one-time setup token from your deployment administrator.' : mode === 'accept' ? 'Use the invited email. Existing accounts keep their current password.' : 'Your team account gives you access to this farm.')}</p>
    <form onSubmit={(event) => void submit(event)}>
      {mode !== 'login' && <label>{t('Your name')}<input required maxLength={100} value={name} autoComplete="name" onChange={(event) => setName(event.target.value)} /></label>}
      <label>{t('Email')}<input required type="email" maxLength={254} value={email} autoComplete="username" onChange={(event) => setEmail(event.target.value)} /></label>
      <label>{t('Password')}<input required type="password" minLength={12} maxLength={128} value={password} autoComplete={mode === 'setup' ? 'new-password' : 'current-password'} onChange={(event) => setPassword(event.target.value)} /></label>
      {mode !== 'login' && <label>{t(mode === 'setup' ? 'Setup token' : 'Invitation token')}<input required type="password" minLength={32} maxLength={256} autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label>}
      {error && <p role="alert" className="form-error">{t(error)}</p>}
      {mode === 'setup' && !session.setup_available && <p role="status">{t('An administrator must configure the one-time setup token on the server first.')}</p>}
      <button className="button primary" disabled={pending || (mode === 'setup' && !session.setup_available)}>{t(pending ? 'Please wait…' : mode === 'login' ? 'Sign in' : mode === 'setup' ? 'Create administrator' : 'Join the team')}</button>
    </form>
    {!session.needs_setup && <button className="text-button" type="button" disabled={pending} onClick={() => { setMode(mode === 'login' ? 'accept' : 'login'); setError(''); setPassword('') }}>{t(mode === 'login' ? 'I have an invitation' : 'Back to sign in')}</button>}
  </section></main>
}
