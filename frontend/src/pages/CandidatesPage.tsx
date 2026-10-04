import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Workflow } from '../types/api'
import { EvidencePanel } from '../components/EvidencePanel'
import { Empty, ErrorState, Loading, StatusBadge } from '../components/States'
import { PageHeading } from '../components/PageHeading'
import { shortId } from '../utils/display'

export function CandidatesPage() {
  const [search] = useSearchParams(); const [workflows, setWorkflows] = useState<Workflow[] | null>(null); const [error, setError] = useState(''); const [expanded, setExpanded] = useState<string | null>(null)
  const load = () => { setWorkflows(null); setError(''); api.workflows(100).then(setWorkflows).catch(e => setError(e instanceof Error ? e.message : 'Unable to load candidates.')) }
  useEffect(load, [])
  const rows = useMemo(() => (workflows ?? []).filter(w => !search.get('workflow') || w.workflow_id === search.get('workflow')).flatMap(workflow => workflow.candidate_matches.map(match => {
    const canonical = workflow.canonical_students.find(item => item.canonical_id === match.canonical_student_id)
    const validated = workflow.validated_profiles.find(item => item.profile.evidence.some(ev => match.assessments.some(a => a.evidence.some(matchEv => matchEv.evidence_id === ev.evidence_id))))
    const byRef = workflow.validated_profiles.find(item => item.profile.name && String(item.profile.name.value) === match.candidate_name)
    return { workflow, match, profile: canonical?.profile ?? validated?.profile ?? byRef?.profile }
  })), [workflows, search])
  return <><PageHeading eyebrow="RESEARCH" title="Candidates" subtitle="Backend match results with source-linked evidence and conservative student classifications." />{error ? <ErrorState error={error} onRetry={load} /> : !workflows ? <Loading label="Loading candidate results…" /> : rows.length === 0 ? <section className="panel"><Empty title="No candidates found yet" children="Candidate results appear here after a workflow completes extraction and matching." /></section> : <div className="candidate-list">{rows.map(({ workflow, match, profile }) => {
    const key = `${workflow.workflow_id}:${match.canonical_student_id}`; const open = expanded === key
    return <article className="panel candidate-card" key={key}><div className="candidate-card-top"><div><div className="candidate-name">{match.candidate_name || 'Name not verified'}</div><small>{profile?.university ? String(profile.university.value) : 'University not verified'} · {shortId(match.canonical_student_id)}</small></div><StatusBadge value={match.status} /></div>
      <div className="classification-row"><div><span className="class-label">FINAL YEAR</span><StatusBadge value={profile?.final_year_status || 'unavailable'} /></div><div><span className="class-label">GENERAL AI INTEREST</span><StatusBadge value={profile?.ai_interest_status || 'unavailable'} /></div></div>
      <div className="match-score"><strong>Match score: {match.match_score}/100</strong><small>{match.score_explanation}</small></div><p className="match-explanation">{match.explanation}</p><div className="skill-chips">{profile?.skills.map((item, i) => <span className="chip" key={`s${i}`}>{String(item.value)}</span>)}{profile?.evidence.filter(e => e.ai_category).map(e => <span className="chip chip-ai" key={e.evidence_id}>{e.ai_category?.replaceAll('_', ' ')}</span>)}</div>
      <div className="candidate-actions"><button className="button button-secondary" aria-expanded={open} onClick={() => setExpanded(open ? null : key)}>{open ? 'Hide evidence' : 'View evidence'} <span aria-hidden="true">{open ? '↑' : '↓'}</span></button>{profile && <details className="profile-summary"><summary>View profile</summary><div><span><b>Degree / branch</b> {profile.degree ? String(profile.degree.value) : 'Unverified'}{profile.branch ? ` · ${String(profile.branch.value)}` : ''}</span><span><b>Location</b> {profile.location ? String(profile.location.value) : 'Unverified'}</span><span><b>Projects</b> {profile.projects.map(item => String(item.value)).join(' · ') || 'None returned'}</span><span><b>Research</b> {profile.research_interests.map(item => String(item.value)).join(' · ') || 'None returned'}</span></div></details>}{workflow.drafts.some(draft => draft.candidate_id === match.canonical_student_id) && <a className="text-link" href={`/emails?workflow=${encodeURIComponent(workflow.workflow_id)}`}>Email review →</a>}<a className="text-link" href={`/workflows/${encodeURIComponent(workflow.workflow_id)}`}>Workflow {shortId(workflow.workflow_id)} →</a></div>
      {open && <div className="candidate-evidence"><div className="evidence-title">MATCH EXPLANATION</div><EvidencePanel match={match} profile={profile} /></div>}
    </article>
  })}</div>}</>
}
