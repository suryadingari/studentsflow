import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './layouts/AppShell'
import { DashboardPage } from './pages/DashboardPage'
import { FindCandidatesPage } from './pages/FindCandidatesPage'
import { WorkflowsPage, WorkflowPage } from './pages/WorkflowsPage'
import { CandidatesPage } from './pages/CandidatesPage'
import { EmailsPage } from './pages/EmailsPage'
import { OutreachPage } from './pages/OutreachPage'
import { AnalyticsPage } from './pages/AnalyticsPage'

export default function App() {
  return <BrowserRouter><Routes><Route element={<AppShell />}>
    <Route path="/" element={<DashboardPage />} /><Route path="/find" element={<FindCandidatesPage />} />
    <Route path="/workflows" element={<WorkflowsPage />} /><Route path="/workflows/:workflowId" element={<WorkflowPage />} />
    <Route path="/candidates" element={<CandidatesPage />} /><Route path="/emails" element={<EmailsPage />} />
    <Route path="/outreach" element={<OutreachPage />} /><Route path="/analytics" element={<AnalyticsPage />} />
    <Route path="*" element={<DashboardPage />} />
  </Route></Routes></BrowserRouter>
}
