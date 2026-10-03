import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Workflow } from '../types/api'
import { Empty, ErrorState, Loading, StatusBadge } from '../components/States'
import { PageHeading } from '../components/PageHeading'
import { date, shortId } from '../utils/display'

export function OutreachPage() {
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null); const [error, setError] = useState('')
  const load = () => { setWorkflows(null); setError(''); api.workflows(100).then(setWorkflows).catch(e => setError(e instanceof Error ? e.message : 'Unable to load outreach.')) }
  useEffect(load, [])
  const records = (workflows ?? []).flatMap(workflow => workflow.outreach_results.map(item => ({ workflow, item, draftId: workflow.outreach_draft_ids[item.outreach_id], draft: workflow.drafts.find(d => d.draft_id === workflow.outreach_draft_ids[item.outreach_id]) })))
  return <><PageHeading eyebrow="CAMPAIGNS" title="Outreach" subtitle="Track approved workflow outcomes, provider metadata, and follow-up plans." />
    <div className="mock-banner"><span className="mock-icon">M</span><div><strong>Demo / Mock Provider</strong><p>All outreach activity is handled by the backend's mock provider. These records do not represent real emails sent.</p></div></div>
    {error ? <ErrorState error={error} onRetry={load} /> : !workflows ? <Loading label="Loading outreach records…" /> : records.length === 0 ? <section className="panel"><Empty title="No outreach activity" children="Approved workflow outcomes and follow-up state appear here when returned by the API." /></section> : <div className="table-panel panel"><div className="table-wrap"><table><thead><tr><th>Recipient</th><th>Approval</th><th>Outreach</th><th>Provider</th><th>Sent / event time</th><th>Follow-up</th></tr></thead><tbody>{records.map(({ workflow, item, draft }) => {
      const follow = workflow.follow_up_results.find(result => result.plan?.original_outreach_id === item.outreach_id)
      return <tr key={item.outreach_id}><td><strong>{draft?.recipient?.email || 'Not supplied'}</strong><small className="table-sub">Workflow {shortId(workflow.workflow_id)}</small></td><td><StatusBadge value={draft?.status || 'unknown'} /></td><td><StatusBadge value={item.status} /></td><td>{item.provider_result?.provider_name || 'MockEmailProvider'}<small className="table-sub">Demo only</small></td><td>{item.provider_result ? date(item.provider_result.timestamp) : item.events.length ? date(item.events[item.events.length - 1].occurred_at) : '—'}</td><td>{follow ? <><StatusBadge value={follow.plan?.status || follow.reply_status} /><small className="table-sub">{follow.explanation}</small></> : <span className="muted">No follow-up result recorded</span>}</td></tr>
    })}</tbody></table></div>{records.map(({ item }) => item.events.length > 0 && <details className="event-details" key={`${item.outreach_id}-events`}><summary>Events for {shortId(item.outreach_id)} ({item.events.length})</summary><div>{item.events.map((event, index) => <span key={`${event.event_type}-${index}`}>{event.event_type.replaceAll('_', ' ')} · {date(event.occurred_at)}</span>)}</div></details>)}</div>}
  </>
}
