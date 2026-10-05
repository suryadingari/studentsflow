import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './layouts/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { FindCandidatesPage } from './pages/FindCandidatesPage'
import { WorkflowsPage, WorkflowPage } from './pages/WorkflowsPage'
import { CandidatesPage } from './pages/CandidatesPage'
import { EmailsPage } from './pages/EmailsPage'
import { OutreachPage } from './pages/OutreachPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { LoginPage } from './pages/LoginPage'
import { api } from './api/client'

const isLocalDemo = import.meta.env.DEV

function initializeLocalDemoUser() {
  sessionStorage.setItem('studentsflow-user', JSON.stringify({
    user_id: 'local-demo', username: 'local-demo', role: 'admin',
  }))
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const user = (() => { try { return JSON.parse(sessionStorage.getItem('studentsflow-user') || '{}') as { role?: string } } catch { return {} } })()
  if (user.role !== 'admin') return <main className="login-shell"><p>Access denied. Admin role required.</p></main>
  return <>{children}</>
}

export default function App() {
  const [authRequired, setAuthRequired] = useState<boolean | null>(() => {
    if (isLocalDemo) initializeLocalDemoUser()
    return isLocalDemo ? false : null
  })
  const [authenticated, setAuthenticated] = useState(
    () => isLocalDemo || Boolean(sessionStorage.getItem('studentsflow-token')),
  )
  useEffect(() => {
    api.authMode().then(mode => {
      // The local Vite development server always opens the demo workspace. The
      // backend still enforces authentication/RBAC for protected API requests.
      if (!isLocalDemo) setAuthRequired(mode.required)
      if (isLocalDemo || !mode.required) {
        // Mirrors the backend's auth-optional local-demo principal. No fake login
        // or bearer token is created; the backend resolves this principal itself.
        initializeLocalDemoUser()
      }
    }).catch(() => {
      // A temporary /auth/mode/API outage must not trap local demo users on
      // "Checking access...". Non-development builds remain fail-closed.
      if (!isLocalDemo) setAuthRequired(true)
    })
    const expired = () => setAuthenticated(false)
    window.addEventListener('studentsflow:unauthorized', expired)
    return () => window.removeEventListener('studentsflow:unauthorized', expired)
  }, [])
  if (authRequired === null) return <main className="login-shell"><p>Checking access…</p></main>
  if (authRequired && !authenticated) return <LoginPage onLogin={() => setAuthenticated(true)} />
  return <BrowserRouter><Routes><Route element={<AppShell />}>
    <Route path="/" element={<DashboardPage />} /><Route path="/find" element={<FindCandidatesPage />} />
    <Route path="/workflows" element={<WorkflowsPage />} /><Route path="/workflows/:workflowId" element={<WorkflowPage />} />
    <Route path="/candidates" element={<CandidatesPage />} /><Route path="/emails" element={<EmailsPage />} />
    <Route path="/outreach" element={<OutreachPage />} /><Route path="/analytics" element={<AdminRoute><AnalyticsPage /></AdminRoute>} />
    <Route path="*" element={<DashboardPage />} />
  </Route></Routes></BrowserRouter>
}
