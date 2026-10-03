import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Kpis } from '../types/api'
import { Empty, ErrorState, Loading } from '../components/States'
import { PageHeading } from '../components/PageHeading'
import { format } from '../utils/display'

const analytics: Array<[string, string, string]> = [
  ['Workflows started', 'workflows_started', '◇'], ['Workflows completed', 'workflows_completed', '✓'], ['Workflows failed', 'workflows_failed', '!'],
  ['Students discovered', 'candidates_discovered', '◎'], ['Final-year verified', 'final_year_verified', '✓'], ['General AI interest supported', 'ai_interest_supported', '✳'],
  ['Candidates matched', 'candidates_matched', '⌕'], ['Emails drafted', 'emails_drafted', '▤'], ['Emails approved', 'emails_approved', '✓'],
  ['Emails rejected', 'emails_rejected', '×'], ['Emails sent (mock)', 'emails_sent', '↗'], ['Replies', 'replies_received', '↩'],
  ['Opt-outs', 'opt_outs', '⊘'], ['Follow-ups planned', 'follow_ups_planned', '◷'],
]
export function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Kpis | null>(null); const [error, setError] = useState('')
  const load = () => { setMetrics(null); setError(''); api.kpis().then(setMetrics).catch(e => setError(e instanceof Error ? e.message : 'Unable to load analytics.')) }
  useEffect(load, [])
  const hasData = metrics ? Object.values(metrics).some(value => value > 0) : false
  return <><PageHeading eyebrow="OBSERVABILITY" title="Analytics" subtitle="Persisted workflow, validation, matching, approval, and outreach aggregates." />
    <div className="notice"><span className="notice-icon" aria-hidden="true">i</span><p>Metrics are read from the backend KPI API. Rate metrics are shown only when the API provides them; counts are not inferred in the browser.</p></div>
    {error ? <ErrorState error={error} onRetry={load} /> : !metrics ? <Loading label="Loading persisted KPI data…" /> : !hasData ? <section className="panel"><Empty title="No persisted KPI data available yet" children="KPI cards will populate from persisted records as workflows are run." /></section> : <><div className="analytics-grid">{analytics.map(([label, key, icon]) => <article className="analytics-card panel" key={key}><span className="analytics-icon" aria-hidden="true">{icon}</span><span>{label}</span><strong>{metrics[key] === undefined ? '—' : format(metrics[key])}</strong><small>{metrics[key] === undefined ? 'Not provided by API' : 'Persisted aggregate'}</small></article>)}</div>
      <section className="panel rate-panel"><div className="panel-heading"><div><h2>Rates returned by API</h2><p>No rates are calculated client-side.</p></div></div>{Object.entries(metrics).filter(([key]) => key.endsWith('_rate')).length ? <div className="rate-list">{Object.entries(metrics).filter(([key]) => key.endsWith('_rate')).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value)}</strong></div>)}</div> : <p className="muted">No rate metrics returned.</p>}</section></>}
  </>
}
