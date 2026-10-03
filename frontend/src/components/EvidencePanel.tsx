import type { CandidateMatch, Evidence, Profile } from '../types/api'
import { StatusBadge } from './States'

export function EvidencePanel({ match, profile }: { match: CandidateMatch; profile?: Profile }) {
  const assessments = match.assessments
  const profileEvidence = profile?.evidence ?? []
  if (!assessments.length && !profileEvidence.length) return <p className="muted">No evidence details were returned for this candidate.</p>
  const evidenceFor = (assessment: CandidateMatch['assessments'][number]): Evidence[] => assessment.evidence
  return <div className="evidence-list">{assessments.map((item, index) => <article className="evidence-item" key={`${item.criterion_type}-${item.value ?? index}`}>
    <div className="evidence-heading"><strong>{item.criterion_type.replaceAll('_', ' ')}{item.value ? ` · ${item.value}` : ''}</strong><StatusBadge value={item.status} /></div>
    <p>{item.explanation}</p>
    {evidenceFor(item).map(ev => <div className="evidence-proof" key={ev.evidence_id}><p>“{ev.supporting_text}”</p><a href={ev.source_url} target="_blank" rel="noreferrer">{ev.source_title || ev.source_url}</a><small>{ev.validation_status} · {ev.strength} strength · {ev.evidence_type}{ev.extracted_at ? ` · observed ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(Date.parse(ev.extracted_at))}` : ''}</small>{ev.final_url && ev.final_url !== ev.source_url && <a href={ev.final_url} target="_blank" rel="noreferrer">Final source URL: {ev.final_url}</a>}</div>)}
    {!item.evidence.length && item.source_urls.map(url => <a key={url} href={url} target="_blank" rel="noreferrer">Source: {url}</a>)}
  </article>)}
    {profileEvidence.length > 0 && <details className="profile-evidence"><summary>All profile evidence ({profileEvidence.length})</summary>{profileEvidence.map(ev => <div className="evidence-proof" key={ev.evidence_id}><strong>{ev.claim_type.replaceAll('_', ' ')}</strong><p>“{ev.supporting_text}”</p><a href={ev.source_url} target="_blank" rel="noreferrer">{ev.source_title || ev.source_url}</a><small>{ev.validation_status} · {ev.evidence_type} · {ev.strength} strength{ev.extracted_at ? ` · observed ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(Date.parse(ev.extracted_at))}` : ''}</small></div>)}</details>}
  </div>
}
