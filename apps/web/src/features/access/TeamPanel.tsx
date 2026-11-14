import { useCallback, useState, type FormEvent } from 'react'
import { ArrowRightStartOnRectangleIcon, UserGroupIcon } from '@heroicons/react/24/outline'
import { accessApi, type TeamMember, type TeamRole } from '../../api/access'
import { useLocale } from '../../shared/locale'
import { ErrorBox, Loading } from '../../shared/ui'
import { useResource } from '../../shared/useResource'
import { useAccess } from './AccessProvider'
import './access.css'

const roles: TeamRole[] = ['admin', 'editor', 'viewer']
const roleLabels = { admin: 'Administrator', editor: 'Editor', viewer: 'Viewer' }
export function TeamPanel() {
  const { t } = useLocale()
  const { session } = useAccess()
  if (session?.mode !== 'team') return <section className="card team-panel">
    <h2><UserGroupIcon aria-hidden="true" />{t('Farm team')}</h2>
    <p>{t('This workspace is in trusted-device mode. Team sign-in must be enabled by the deployment administrator before sharing access.')}</p>
    <p className="muted">{t('Team mode supports administrators, editors and viewers, with individual accounts and revocable invitations.')}</p>
  </section>
  return <ActiveTeamPanel />
}

function ActiveTeamPanel() {
  const { t, date } = useLocale()
  const { session, isAdmin, refresh } = useAccess()
  const resource = useResource(useCallback((signal: AbortSignal) => accessApi.team(signal), []))
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<TeamRole>('viewer')
  const [link, setLink] = useState('')
  const [copied, setCopied] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordSaved, setPasswordSaved] = useState(false)
  async function action(run: () => Promise<unknown>) {
    setPending(true); setError('')
    try { await run(); resource.reload(); await refresh() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to update team access.') }
    finally { setPending(false) }
  }
  function invite(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const result = await accessApi.invite({ email, role })
      setLink(location.origin + '/#invite=' + encodeURIComponent(result.token)); setCopied(false); setEmail('')
    })
  }
  return <section className="card team-panel">
    <header><div><h2><UserGroupIcon aria-hidden="true" />{t('Farm team')}</h2><p>{session?.user?.name} · {t(roleLabels[session?.role ?? 'viewer'])}</p></div>
      <button className="button" disabled={pending} onClick={() => void action(() => accessApi.logout())}><ArrowRightStartOnRectangleIcon aria-hidden="true" />{t('Sign out')}</button></header>
    <p>{t('Administrators manage people. Editors update fields, run analyses and use the assistant. Viewers can inspect and download saved results.')}</p>
    {error && <p role="alert" className="form-error">{t(error)}</p>}
    {resource.pending && <Loading text={t('Loading team…')} />}
    {resource.error && <ErrorBox message={resource.error} retry={resource.reload} />}
    <ul className="team-members">{resource.data?.members.map((member) => <MemberRow key={member.id} member={member} canManage={isAdmin} pending={pending}
      update={(nextRole, active) => void action(() => accessApi.member(member.id, nextRole, active))} />)}</ul>
    {isAdmin && <>
      <form className="team-invite" onSubmit={invite}>
        <label>{t('Invite by email')}<input type="email" required value={email} maxLength={254} onChange={(event) => setEmail(event.target.value)} /></label>
        <label>{t('Role')}<select aria-label={t('Role')} value={role} onChange={(event) => setRole(event.target.value as TeamRole)}>{roles.map((item) => <option key={item} value={item}>{t(roleLabels[item])}</option>)}</select></label>
        <button className="button primary" disabled={pending}>{t('Create invitation')}</button>
      </form>
      {link && <div className="team-share" role="status"><p>{t('Share this private invitation with the intended person. It expires in seven days and can be used once.')}</p>
        <input readOnly aria-label={t('Invitation link')} value={link} onFocus={(event) => event.target.select()} />
        <button className="button" onClick={() => void navigator.clipboard.writeText(link).then(() => setCopied(true)).catch(() => setError('Copy the selected link manually.'))}>{t(copied ? 'Copied' : 'Copy link')}</button></div>}
      {Boolean(resource.data?.invitations.length) && <div className="team-invitations"><h3>{t('Pending invitations')}</h3>{resource.data?.invitations.map((item) => <div key={item.id}>
        <span>{item.email}<small>{t(roleLabels[item.role])} · {date(item.expires_at)}</small></span>
        <button className="button" disabled={pending} onClick={() => void action(async () => { await accessApi.revoke(item.id); setLink('') })}>{t('Revoke')}</button>
      </div>)}</div>}
    </>}
    <details className="team-password"><summary>{t('Change password')}</summary>
      <form onSubmit={(event) => { event.preventDefault(); void action(async () => { await accessApi.password({ current_password: currentPassword, new_password: newPassword }); setCurrentPassword(''); setNewPassword(''); setPasswordSaved(true) }) }}>
        <label>{t('Current password')}<input required type="password" autoComplete="current-password" minLength={12} maxLength={128} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <label>{t('New password')}<input required type="password" autoComplete="new-password" minLength={12} maxLength={128} value={newPassword} onChange={(event) => { setNewPassword(event.target.value); setPasswordSaved(false) }} /></label>
        <button className="button" disabled={pending}>{t('Save password')}</button>
      </form>
      {passwordSaved && <p role="status">{t('Password changed. Other sessions have been signed out.')}</p>}
    </details>
  </section>
}

function MemberRow({ member, canManage, pending, update }: { member: TeamMember; canManage: boolean; pending: boolean; update: (role: TeamRole, active: boolean) => void }) {
  const { t } = useLocale()
  return <li><div><strong>{member.name}</strong><small>{member.email}</small></div>
    {canManage ? <><select aria-label={t('Role') + ' · ' + member.name} value={member.role} disabled={pending || !member.active} onChange={(event) => update(event.target.value as TeamRole, member.active)}>{roles.map((role) => <option key={role} value={role}>{t(roleLabels[role])}</option>)}</select>
      <button className="button" disabled={pending} onClick={() => update(member.role, !member.active)}>{t(member.active ? 'Remove access' : 'Restore access')}</button></>
      : <span className="chip">{t(member.active ? roleLabels[member.role] : 'Access removed')}</span>}
  </li>
}
