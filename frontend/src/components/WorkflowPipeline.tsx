import type { PersistedStep, WorkflowStep } from '../types/api'
import { StatusBadge } from './States'

const stages: Array<[string, string]> = [
  ['research', 'Research · discovery, crawl, extraction, validation'],
  ['deduplication', 'Deduplication'], ['enrichment', 'Enrichment'],
  ['matching', 'Matching'], ['email_drafting', 'Email Drafting'], ['human_approval', 'Human Approval'],
  ['outreach', 'Outreach'], ['follow_up', 'Follow-up'],
]
export function WorkflowPipeline({ steps }: { steps: Array<WorkflowStep | PersistedStep> }) {
  return <ol className="pipeline">{stages.map(([key, label]) => {
    const matched = steps.filter(step => step.step_type === key)
    const display = matched[matched.length - 1]
    const state = display?.state
    return <li className={`pipeline-item state-${state}`} key={key}>
      <span className="pipeline-dot" aria-hidden="true" />
      <div className="pipeline-label"><strong>{label}</strong>{display?.agent_version && <small>v{display.agent_version}</small>}
        {display?.error && <small className="text-danger">{display.error}</small>}</div>
      <StatusBadge value={!state ? 'not started' : state === 'succeeded' ? 'completed' : state === 'blocked' ? 'failed' : state} />
    </li>
  })}</ol>
}
