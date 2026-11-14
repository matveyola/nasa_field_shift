import { request } from './client'

export type TeamRole = 'admin' | 'editor' | 'viewer'
export type AccessSession = {
  mode: 'local' | 'team'
  authenticated: boolean
  needs_setup: boolean
  setup_available: boolean
  user: { id: number | null; name: string; email: string | null } | null
  role: TeamRole | null
  csrf_token: string | null
}
export type TeamMember = { id: number; name: string; email: string; role: TeamRole; active: boolean }
export type Invitation = { id: string; email: string; role: TeamRole; expires_at: string }
export type Team = { members: TeamMember[]; invitations: Invitation[] }
type Credentials = { email: string; password: string }
type Registration = Credentials & { name: string; token: string }
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })

export const accessApi = {
  session: (signal?: AbortSignal) => request<AccessSession>('/auth/session', { signal }),
  login: (body: Credentials) => request('/auth/login', post(body)),
  setup: (body: Registration) => request('/auth/setup', post(body)),
  accept: (body: Registration) => request('/auth/accept', post(body)),
  logout: () => request('/auth/logout', post({})),
  password: (body: { current_password: string; new_password: string }) => request('/auth/password', post(body)),
  team: (signal?: AbortSignal) => request<Team>('/team', { signal }),
  invite: (body: { email: string; role: TeamRole }) => request<Invitation & { token: string }>('/team/invitations', post(body)),
  revoke: (id: string) => request('/team/invitations/' + encodeURIComponent(id), { method: 'DELETE' }),
  member: (id: number, role: TeamRole, active: boolean) => request('/team/members/' + id, { method: 'PATCH', body: JSON.stringify({ role, active }) }),
}
