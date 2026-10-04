import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { ErrorState } from '../components/States'

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setBusy(true)
    const data = new FormData(event.currentTarget)
    try { await api.login(String(data.get('username') || ''), String(data.get('password') || '')); onLogin() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign in failed.') }
    finally { setBusy(false) }
  }
  return <main className="login-shell"><form className="panel login-panel" onSubmit={submit}><div className="brand"><span className="brand-icon">C</span><span>students<span className="brand-light">flow</span><small>SECURE RESEARCH WORKSPACE</small></span></div><h1>Sign in</h1><p>Use your assigned StudentsFlow account.</p>{error && <ErrorState error={error} />}<div className="field"><label htmlFor="username">Username</label><input id="username" name="username" autoComplete="username" required /></div><div className="field"><label htmlFor="password">Password</label><input id="password" name="password" type="password" autoComplete="current-password" required /></div><button className="button button-primary button-wide" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button><small>Session expires after 30 minutes and is held only in this browser tab.</small></form></main>
}
