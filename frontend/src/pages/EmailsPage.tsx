import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Draft, Workflow } from '../types/api'
import { Empty, ErrorState, Loading, StatusBadge } from '../components/States'
import { PageHeading } from '../components/PageHeading'
import { date, shortId } from '../utils/display'

interface DraftItem { workflow: Workflow; draft: Draft }
export function EmailsPage() {
  const [params] = useSearchParams(); const [items, setItems] = useState<DraftItem[] | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [editId, setEditId] = useState(''); const [actor, setActor] = useState(''); const [notice, setNotice] = useState('')
  const load = () => { setError(''); setItems(null); api.workflows(100).then(all => setItems(all.filter(w => !params.get('workflow') || w.workflow_id === params.get('workflow')).flatMap(workflow => workflow.drafts.map(draft => ({ workflow, draft }))))).catch(e => setError(e instanceof Error ? e.message : 'Unable to load email drafts.')) }
  useEffect(load, [])
  async function act(item: DraftItem, action: 'approve' | 'reject' | 'edit', event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault(); const form = event ? new FormData(event.currentTarget) : null
    const reviewer = String(form?.get('actor_id') || actor).trim()
    if (!reviewer) { setError('Enter an approval actor ID before taking a review action.'); return }
    setBusy(item.draft.draft_id); setError(''); setNotice('')
    try {
      await api.approval(item.workflow.workflow_id, item.draft.draft_id, action, {
        actor_id: reviewer,
        ...(action === 'edit' ? { subject: String(form?.get('subject') || ''), body: String(form?.get('body') || '') } : {}),
        ...(action === 'reject' ? { reason: String(form?.get('reason') || 'Rejected during human review') } : {}),
      })
      setActor(reviewer); setEditId(''); setNotice(action === 'approve' ? 'Approval recorded. The workflow resumed through the configured mock provider.' : action === 'edit' ? 'Edit saved as a new review version. Approval is still required.' : 'Draft rejected; no outreach was approved.')
      load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Review action failed.') }
    finally { setBusy('') }
  }
  return <><PageHeading eyebrow="HUMAN REVIEW" title="Email approval" subtitle="Review evidence-grounded drafts. Human approval is required before the workflow proceeds." />
    <div className="approval-banner"><strong>Human approval required</strong><span>Approving resumes the workflow. This environment is configured with MockEmailProvider; no real email provider is connected.</span></div>
    {notice && <div className="success-banner">{notice}</div>}{error && <ErrorState error={error} />}{!items ? <Loading label="Loading email drafts…" /> : items.length === 0 ? <section className="panel"><Empty title="No email drafts" children="Drafts returned by workflow runs will appear here for human review." /></section> : <div className="email-list">{items.map(item => {
      const { draft, workflow } = item; const pending = draft.status === 'pending_review' || draft.status === 'edited'; const editing = editId === draft.draft_id
      return <article className="panel email-card" key={draft.draft_id}><div className="email-header"><div><div className="eyebrow">DRAFT · {shortId(draft.draft_id)}</div><h2>{draft.recipient?.email || 'Recipient not supplied'}</h2><small>Workflow {shortId(workflow.workflow_id)} · Updated {date(draft.versions.at(-1)?.created_at || workflow.updated_at)}</small></div><StatusBadge value={draft.status} /></div>
        <div className="email-meta"><span><b>To</b> {draft.recipient?.email || 'Not available'}</span><span><b>Subject</b> {draft.subject || 'No subject'}</span></div>
        {editing ? <form className="edit-form" onSubmit={event => act(item, 'edit', event)}><div className="field"><label htmlFor={`subject-${draft.draft_id}`}>Subject</label><input id={`subject-${draft.draft_id}`} name="subject" required defaultValue={draft.subject} /></div><div className="field"><label htmlFor={`body-${draft.draft_id}`}>Message</label><textarea id={`body-${draft.draft_id}`} name="body" rows={8} required defaultValue={draft.body} /></div><Reviewer name="actor_id" /><div className="candidate-actions"><button className="button button-primary" disabled={busy === draft.draft_id}>Save edit</button><button className="button button-secondary" type="button" onClick={() => setEditId('')}>Cancel</button></div></form> : <div className="email-body">{draft.body || 'No message body returned.'}</div>}
        {draft.versions.length > 0 && <details className="version-history"><summary>Version history ({draft.versions.length})</summary>{draft.versions.map(version => <div className="version-entry" key={version.version_number}><strong>Version {version.version_number}</strong><small>{version.authored_by} · {date(version.created_at)}</small><p>{version.subject}</p><pre>{version.body}</pre></div>)}</details>}
        {draft.approvals.length > 0 && <div className="approval-history"><strong>Review history</strong>{draft.approvals.map(approval => <span key={approval.action_id}>{approval.action.replaceAll('_', ' ')} by {approval.actor_id} · {date(approval.occurred_at)}</span>)}</div>}
        {pending && !editing && <div className="review-actions"><div className="reviewer-inline"><label htmlFor={`reviewer-${draft.draft_id}`}>Reviewer ID</label><input id={`reviewer-${draft.draft_id}`} value={actor} onChange={event => setActor(event.target.value)} placeholder="Enter your reviewer ID" /></div><button className="button button-secondary" disabled={Boolean(busy)} onClick={() => setEditId(draft.draft_id)}>Edit</button><button className="button button-danger" disabled={Boolean(busy)} onClick={() => act(item, 'reject')}>Reject</button><button className="button button-primary" disabled={Boolean(busy)} onClick={() => act(item, 'approve')}>{busy === draft.draft_id ? 'Saving…' : 'Approve'}</button></div>}
      </article>
    })}</div>}</>
}
function Reviewer({ name }: { name: string }) { return <div className="field"><label htmlFor="edit-reviewer">Reviewer ID</label><input id="edit-reviewer" name={name} required placeholder="Your reviewer ID" /></div> }
