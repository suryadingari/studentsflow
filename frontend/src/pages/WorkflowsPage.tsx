import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { AuditEvent, PersistedStep, Workflow } from '../types/api'
import { Empty, ErrorState, Loading, StatusBadge } from '../components/States'
import { WorkflowPipeline } from '../components/WorkflowPipeline'
import { PageHeading } from '../components/PageHeading'
import { date, shortId } from '../utils/display'

export function WorkflowsPage() {
  const [items, setItems] = useState<Workflow[] | null>(null); const [error, setError] = useState('')
  const load = () => { setItems(null); setError(''); api.workflows(100).then(setItems).catch(e => setError(e instanceof Error ? e.message : 'Unable to load workflows.')) }
  useEffect(load, [])
  return <><PageHeading eyebrow="OPERATIONS" title="Workflows" subtitle="Review persisted workflow runs, execution state, and results." action={<button className="button button-secondary" disabled={!items} onClick={load}>Refresh list</button>} />{error ? <ErrorState error={error} onRetry={load} /> : !items ? <Loading label="Loading workflows…" /> : items.length === 0 ? <section className="panel"><Empty title="No workflows yet" children="Start a candidate research workflow to see it here." /><div className="center-action"><Link className="button button-primary" to="/find">Find candidates</Link></div></section> : <div className="workflow-list">{items.map(workflow => <Link className="workflow-row panel" to={`/workflows/${encodeURIComponent(workflow.workflow_id)}`} key={workflow.workflow_id}><div className="workflow-row-main"><span className="workflow-mark" aria-hidden="true">⌘</span><div><strong>Workflow {shortId(workflow.workflow_id)}</strong><small>Requirement {shortId(workflow.requirement_id)} · {date(workflow.created_at)}</small></div></div><div className="workflow-row-info"><span>{workflow.metrics.candidates_discovered ?? 0} profiles</span><span>{workflow.metrics.candidates_matched ?? 0} matches</span><StatusBadge value={workflow.status} /><span aria-hidden="true">→</span></div></Link>)}</div>}</>
}

export function WorkflowPage() {
  const { workflowId = '' } = useParams(); const [workflow, setWorkflow] = useState<Workflow | null>(null); const [steps, setSteps] = useState<PersistedStep[] | null>(null); const [audit, setAudit] = useState<AuditEvent[] | null>(null); const [error, setError] = useState('')
  const load = () => { setError(''); setWorkflow(null); setSteps(null); setAudit(null); Promise.all([api.workflow(workflowId), api.steps(workflowId), api.audit(workflowId)]).then(([result, persisted, events]) => { setWorkflow(result); setSteps(persisted); setAudit(events) }).catch(e => setError(e instanceof Error ? e.message : 'Unable to load workflow.')) }
  useEffect(load, [workflowId])
  useEffect(() => {
    if (!workflow || !['created', 'running'].includes(workflow.status)) return
    const timer = window.setInterval(() => {
      Promise.all([api.workflow(workflowId), api.steps(workflowId), api.audit(workflowId)])
        .then(([result, persisted, events]) => { setWorkflow(result); setSteps(persisted); setAudit(events) })
        .catch(e => setError(e instanceof Error ? e.message : 'Unable to refresh workflow progress.'))
    }, 3000)
    return () => window.clearInterval(timer)
  }, [workflowId, workflow?.status])
  if (error) return <><PageHeading eyebrow="WORKFLOW" title="Workflow details" subtitle={shortId(workflowId)} /><ErrorState error={error} onRetry={load} /></>
  if (!workflow || !steps) return <><PageHeading eyebrow="WORKFLOW" title="Workflow details" subtitle={shortId(workflowId)} /><Loading label="Loading workflow…" /></>
  return <><PageHeading eyebrow="WORKFLOW DETAILS" title={`Workflow ${shortId(workflow.workflow_id)}`} subtitle={`Requirement ${workflow.requirement_id}`} action={<div className="workflow-header-actions"><button className="button button-secondary" disabled={!workflow || !steps} onClick={load}>Refresh state</button><StatusBadge value={workflow.status} /></div>} />
    {workflow.status === 'waiting_for_approval' && <div className="approval-banner"><strong>Human approval required</strong><span>Review each draft before the workflow can continue. No automatic outreach approval occurs.</span></div>}
    <div className="detail-grid"><section className="panel detail-summary"><div className="panel-heading"><div><h2>Run summary</h2><p>Persisted workflow metadata</p></div></div><dl className="meta-grid"><Meta label="Workflow ID" value={workflow.workflow_id} /><Meta label="Status" value={workflow.status.replaceAll('_', ' ')} /><Meta label="Created" value={date(workflow.created_at)} /><Meta label="Updated" value={date(workflow.updated_at)} /><Meta label="Current step" value={workflow.current_step?.replaceAll('_', ' ') || '—'} /><Meta label="Candidates matched" value={String(workflow.metrics.candidates_matched ?? 0)} /></dl></section>
      <section className="panel pipeline-panel"><div className="panel-heading"><div><h2>Agent pipeline</h2><p>Execution state from persisted workflow steps</p></div><span className="step-count">Job {shortId(workflow.job_id || workflow.workflow_id)} · {steps.length} steps</span></div>{steps.length ? <WorkflowPipeline steps={steps} /> : <Empty title="No persisted steps returned" />}</section></div>
    {workflow.source_discovery && <section className="panel source-results"><div className="panel-heading"><div><h2>Source discovery</h2><p>{workflow.source_discovery.search_performed ? `Search performed by ${workflow.source_discovery.search_provider}` : 'No external search claimed'}</p></div><span className="step-count">{workflow.source_discovery.candidates.length} accepted</span></div><p>{workflow.source_discovery.note}</p><div className="source-links">{workflow.source_discovery.candidates.map(source => <a href={source.url} target="_blank" rel="noreferrer" key={`${source.origin}:${source.url}`}><strong>{source.origin === 'search_discovered' ? 'SEARCH_DISCOVERED' : 'USER_SUPPLIED'}</strong><span>{source.url}</span></a>)}</div>{workflow.source_discovery.rejected.length > 0 && <details><summary>{workflow.source_discovery.rejected.length} rejected source(s)</summary><ul>{workflow.source_discovery.rejected.map((source, index) => <li key={`${source.url}-${index}`}>{source.url} — {source.reason}</li>)}</ul></details>}</section>}
    {audit && <section className="panel source-results"><div className="panel-heading"><div><h2>Audit trail</h2><p>Persisted workflow events with agent version and provenance metadata</p></div><span className="step-count">{audit.length} events</span></div>{audit.length ? <div className="audit-events">{audit.map(event => <article key={event.audit_id}><div><strong>{event.event_type.replaceAll('_', ' ')}</strong><small>{event.agent_name} v{event.agent_version} · {date(event.occurred_at)}</small></div><pre>{JSON.stringify(event.details, null, 2)}</pre></article>)}</div> : <Empty title="No audit events returned" />}</section>}
    {workflow.errors.length > 0 && <section className="panel"><div className="panel-heading"><h2>Workflow messages</h2></div><ul className="error-list">{workflow.errors.map((message, index) => <li key={index}>{message}</li>)}</ul></section>}
    <div className="workflow-result-links"><Link className="button button-secondary" to={`/candidates?workflow=${encodeURIComponent(workflow.workflow_id)}`}>View candidates <span aria-hidden="true">→</span></Link><Link className="button button-secondary" to={`/emails?workflow=${encodeURIComponent(workflow.workflow_id)}`}>Review email drafts <span aria-hidden="true">→</span></Link></div>
    <div className="panel inline-results"><div><strong>{workflow.candidate_matches.length} match records</strong><span> · {workflow.validated_profiles.length} validated profiles · {workflow.drafts.length} drafts</span></div><p>Open Candidates or Email review to inspect evidence and human approval state.</p></div>
  </>
}
function Meta({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd title={value}>{value}</dd></div> }
