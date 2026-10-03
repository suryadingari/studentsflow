import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Kpis, Workflow } from '../types/api'
import { Empty, ErrorState, Loading, StatusBadge } from '../components/States'
import { PageHeading } from '../components/PageHeading'
import { date, format, shortId } from '../utils/display'

const cards: Array<[string, string]> = [
  ['Workflows', 'workflows_started'], ['Students discovered', 'candidates_discovered'], ['Final-year verified', 'final_year_verified'],
  ['General AI interest supported', 'ai_interest_supported'], ['Candidates matched', 'candidates_matched'], ['Emails approved', 'emails_approved'],
  ['Outreach sent', 'emails_sent'], ['Replies', 'replies_received'],
]
export function DashboardPage() {
  const [kpis, setKpis] = useState<Kpis | null>(null); const [workflows, setWorkflows] = useState<Workflow[] | null>(null); const [error, setError] = useState('')
  const load = () => { setError(''); setKpis(null); setWorkflows(null); Promise.all([api.kpis(), api.workflows(6)]).then(([metrics, recent]) => { setKpis(metrics); setWorkflows(recent) }).catch(e => setError(e instanceof Error ? e.message : 'Unable to load dashboard.')) }
  useEffect(load, [])
  if (error) return <><PageHeading eyebrow="OVERVIEW" title="Dashboard" subtitle="Monitor evidence-led candidate research and review activity." /><ErrorState error={error} onRetry={load} /></>
  if (!kpis || !workflows) return <><PageHeading eyebrow="OVERVIEW" title="Dashboard" subtitle="Monitor evidence-led candidate research and review activity." /><Loading label="Loading dashboard…" /></>
  const hasData = Object.values(kpis).some(value => value > 0) || workflows.length > 0
  return <><PageHeading eyebrow="OVERVIEW" title="Dashboard" subtitle="Monitor evidence-led candidate research and review activity." action={<Link className="button button-primary" to="/find"><span aria-hidden="true">＋</span> Find candidates</Link>} />
    <div className="notice"><span className="notice-icon" aria-hidden="true">i</span><p>Candidate interest is shown only when supported by public or authorized evidence. Final-year status and AI evidence remain separate classifications.</p></div>
    <div className="section-heading"><div><h2>Key metrics</h2><p>Persisted workflow and audit data</p></div><span className="fresh-label"><span className="live-dot" /> Live from API</span></div>
    <div className="kpi-grid">{cards.map(([label, key], index) => { const value = kpis[key]; return <article className="kpi-card" key={key}><div className="kpi-top"><span>{label}</span><span className={`kpi-symbol symbol-${index % 4}`} aria-hidden="true">{['◷', '◎', '✓', '✳'][index % 4]}</span></div><strong>{!hasData || value === undefined ? 'No data yet' : format(value)}</strong><small>{value !== undefined && value > 0 ? 'From persisted records' : 'Awaiting workflow activity'}</small></article> })}</div>
    <section className="panel recent-panel"><div className="panel-heading"><div><h2>Recent workflows</h2><p>Latest persisted runs and review state</p></div><Link className="text-link" to="/workflows">View all <span aria-hidden="true">→</span></Link></div>
      {workflows.length ? <div className="table-wrap"><table><thead><tr><th>Workflow</th><th>Status</th><th>Current step</th><th>Created</th><th /></tr></thead><tbody>{workflows.map(w => <tr key={w.workflow_id}><td><strong>{shortId(w.workflow_id)}</strong><small className="table-sub">Requirement {shortId(w.requirement_id)}</small></td><td><StatusBadge value={w.status} /></td><td>{w.current_step?.replaceAll('_', ' ') || '—'}</td><td>{date(w.created_at)}</td><td><Link className="row-link" to={`/workflows/${encodeURIComponent(w.workflow_id)}`}>Open <span aria-hidden="true">→</span></Link></td></tr>)}</tbody></table></div> : <Empty title="No data yet" children="Create a workflow to see persisted research activity here." />}
    </section>
  </>
}
