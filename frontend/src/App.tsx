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

export default function App() {
  const [authRequired, setAuthRequired] = useState<boolean | null>(null)
  const [authenticated, setAuthenticated] = useState(Boolean(sessionStorage.getItem('studentsflow-token')))
  useEffect(() => {
    api.authMode().then(mode => setAuthRequired(mode.required)).catch(() => setAuthRequired(true))
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
    <Route path="/outreach" element={<OutreachPage />} /><Route path="/analytics" element={<AnalyticsPage />} />
    <Route path="*" element={<DashboardPage />} />
  </Route></Routes></BrowserRouter>
}
