import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { PersistedStep, Workflow } from '../types/api'
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
  const { workflowId = '' } = useParams(); const [workflow, setWorkflow] = useState<Workflow | null>(null); const [steps, setSteps] = useState<PersistedStep[] | null>(null); const [error, setError] = useState('')
  const load = () => { setError(''); setWorkflow(null); setSteps(null); Promise.all([api.workflow(workflowId), api.steps(workflowId)]).then(([result, persisted]) => { setWorkflow(result); setSteps(persisted) }).catch(e => setError(e instanceof Error ? e.message : 'Unable to load workflow.')) }
  useEffect(load, [workflowId])
  if (error) return <><PageHeading eyebrow="WORKFLOW" title="Workflow details" subtitle={shortId(workflowId)} /><ErrorState error={error} onRetry={load} /></>
  if (!workflow || !steps) return <><PageHeading eyebrow="WORKFLOW" title="Workflow details" subtitle={shortId(workflowId)} /><Loading label="Loading workflow…" /></>
  return <><PageHeading eyebrow="WORKFLOW DETAILS" title={`Workflow ${shortId(workflow.workflow_id)}`} subtitle={`Requirement ${workflow.requirement_id}`} action={<div className="workflow-header-actions"><button className="button button-secondary" disabled={!workflow || !steps} onClick={load}>Refresh state</button><StatusBadge value={workflow.status} /></div>} />
    {workflow.status === 'waiting_for_approval' && <div className="approval-banner"><strong>Human approval required</strong><span>Review each draft before the workflow can continue. No automatic outreach approval occurs.</span></div>}
    <div className="detail-grid"><section className="panel detail-summary"><div className="panel-heading"><div><h2>Run summary</h2><p>Persisted workflow metadata</p></div></div><dl className="meta-grid"><Meta label="Workflow ID" value={workflow.workflow_id} /><Meta label="Status" value={workflow.status.replaceAll('_', ' ')} /><Meta label="Created" value={date(workflow.created_at)} /><Meta label="Updated" value={date(workflow.updated_at)} /><Meta label="Current step" value={workflow.current_step?.replaceAll('_', ' ') || '—'} /><Meta label="Candidates matched" value={String(workflow.metrics.candidates_matched ?? 0)} /></dl></section>
      <section className="panel pipeline-panel"><div className="panel-heading"><div><h2>Agent pipeline</h2><p>Execution state from persisted workflow steps</p></div><span className="step-count">{steps.length} recorded</span></div>{steps.length ? <WorkflowPipeline steps={steps} /> : <Empty title="No persisted steps returned" />}</section></div>
    {workflow.errors.length > 0 && <section className="panel"><div className="panel-heading"><h2>Workflow messages</h2></div><ul className="error-list">{workflow.errors.map((message, index) => <li key={index}>{message}</li>)}</ul></section>}
    <div className="workflow-result-links"><Link className="button button-secondary" to={`/candidates?workflow=${encodeURIComponent(workflow.workflow_id)}`}>View candidates <span aria-hidden="true">→</span></Link><Link className="button button-secondary" to={`/emails?workflow=${encodeURIComponent(workflow.workflow_id)}`}>Review email drafts <span aria-hidden="true">→</span></Link></div>
    <div className="panel inline-results"><div><strong>{workflow.candidate_matches.length} match records</strong><span> · {workflow.validated_profiles.length} validated profiles · {workflow.drafts.length} drafts</span></div><p>Open Candidates or Email review to inspect evidence and human approval state.</p></div>
  </>
}
function Meta({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd title={value}>{value}</dd></div> }
