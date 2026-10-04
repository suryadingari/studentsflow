import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Criterion, WorkflowRequest } from '../types/api'
import { ErrorState } from '../components/States'
import { PageHeading } from '../components/PageHeading'

const values = (raw: string) => raw.split(',').map(value => value.trim()).filter(Boolean)
export function FindCandidatesPage() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [created, setCreated] = useState(''); const navigate = useNavigate()
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setCreated('')
    const data = new FormData(event.currentTarget)
    const title = String(data.get('title') || '').trim(); const description = String(data.get('description') || '').trim()
    const allowed = values(String(data.get('domains') || '')).map(item => item.toLowerCase().replace(/^https?:\/\//, '').replace(/\/$/, ''))
    const urls = values(String(data.get('sources') || ''))
    const searchEnabled = data.get('search_enabled') === 'on'
    if (!urls.length && !searchEnabled) { setError('Add at least one public source URL or enable configured source search.'); setBusy(false); return }
    const criteria: Criterion[] = [{ criterion_type: 'final_year', required: true }, { criterion_type: 'ai_interest', required: true }]
    const add = (field: string, kind: Criterion['criterion_type'], required: boolean) => values(String(data.get(field) || '')).forEach(value => criteria.push({ criterion_type: kind, value, required }))
    add('location', 'location', false); add('university', 'university', false); add('degree', 'degree', false); add('branch', 'branch', false)
    add('required_skills', 'skill', true); add('preferred_skills', 'skill', false); add('required_ai', 'ai_area', true); add('preferred_ai', 'ai_area', false)
    add('required_projects', 'project', true); add('preferred_projects', 'project', false); add('research', 'research', true); add('experience', 'experience', false)
    const sources = urls.map(url => {
      let domain = ''
      try { domain = new URL(url).hostname.toLowerCase() } catch { return null }
      return { url, domain, source_type: String(data.get('source_type') || 'other'), reason: `User supplied source for: ${title}`, access: String(data.get('source_access') || 'public') as 'public' | 'authorized' }
    })
    if (sources.some(source => source === null)) { setError('Every source must be a complete http or https URL.'); setBusy(false); return }
    const missing = [...new Set(sources.filter((source): source is NonNullable<typeof source> => source !== null).map(source => source.domain).filter(domain => !allowed.some(item => domain === item || domain.endsWith(`.${item}`))))]
    if (missing.length) { setError(`Add each source hostname to the permitted domains: ${missing.join(', ')}`); setBusy(false); return }
    const payload: WorkflowRequest = { requirement: { raw_text: `${title}${description ? ` — ${description}` : ''}`, criteria }, sources: sources.filter((source): source is NonNullable<typeof source> => source !== null), permitted_domains: allowed, crawl_configuration: { allowed_domains: allowed, max_pages: 5, max_depth: 1 }, demo_mode: data.get('demo_mode') === 'on', search_enabled: searchEnabled, max_search_results: 10 }
    try { const workflow = await api.createWorkflow(payload); setCreated(workflow.workflow_id); navigate(`/workflows/${encodeURIComponent(workflow.workflow_id)}`) }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to start workflow.') }
    finally { setBusy(false) }
  }
  return <><PageHeading eyebrow="DISCOVERY" title="Find candidates" subtitle="Define explicit criteria and submit public or authorized source URLs for evidence-based research." />
    <div className="notice"><span className="notice-icon" aria-hidden="true">i</span><p>AI interest is an independent evidence requirement. The backend will not infer it from a degree, branch, institution, or generic programming skills. Real public crawl is the default; mock mode is explicitly labeled for demonstrations.</p></div>
    {created && <div className="success-banner">Workflow created. <Link to={`/workflows/${encodeURIComponent(created)}`}>Open workflow</Link></div>}
    {error && <ErrorState error={error} />}
    <form className="form-layout" onSubmit={submit}>
      <section className="panel form-panel"><div className="panel-heading"><div><h2>Requirement</h2><p>Describe the target population, then select structured criteria.</p></div><span className="step-number">01</span></div>
        <div className="field"><label htmlFor="title">Requirement name <span className="required">*</span></label><input id="title" name="title" required placeholder="e.g. Final-year students with computer vision experience" /></div>
        <div className="field"><label htmlFor="description">Description</label><textarea id="description" name="description" rows={3} placeholder="Add context for this research requirement" /></div>
        <div className="form-section-title">Education & location <span>Optional preferences</span></div>
        <div className="field-grid"><TextField label="Location" name="location" placeholder="India, Bengaluru" /><TextField label="University" name="university" placeholder="University name" /><TextField label="Degree" name="degree" placeholder="B.Tech, B.Sc" /><TextField label="Branch" name="branch" placeholder="Computer Science, Mechanical" /></div>
        <div className="form-section-title">Technical evidence <span>Comma-separated values</span></div>
        <div className="field-grid"><TextField label="Required skills" name="required_skills" placeholder="Python, PyTorch" /><TextField label="Preferred skills" name="preferred_skills" placeholder="OpenCV, TensorFlow" /><TextField label="Required AI specializations" name="required_ai" placeholder="Optional: Computer Vision" /><TextField label="Preferred AI specializations" name="preferred_ai" placeholder="Optional: NLP, Generative AI" /><TextField label="Required projects" name="required_projects" placeholder="Optional: Object detection" /><TextField label="Preferred projects" name="preferred_projects" placeholder="Image classification" /><TextField label="Required research" name="research" placeholder="AI publications or research" /><TextField label="Preferred experience" name="experience" placeholder="AI internship, hackathon" /></div>
        <div className="classification-note"><span aria-hidden="true">✓</span><span><strong>General AI interest or experience is the required baseline.</strong><br />Specializations such as Computer Vision, NLP, or Generative AI are optional filters and none is required by default. The backend validates status from source evidence.</span></div>
      </section>
      <aside className="form-aside"><section className="panel form-panel"><div className="panel-heading"><div><h2>Source scope</h2><p>Only include sources you may access.</p></div><span className="step-number">02</span></div>
        <div className="field"><label htmlFor="sources">Permitted public source URLs</label><textarea id="sources" name="sources" rows={6} placeholder={'https://example.edu/profile\nhttps://github.com/example'} /><small>One URL per line. URL mode is always available. Do not submit private, restricted, or login-gated sources.</small></div>
        <label className="checkbox-field"><input type="checkbox" name="search_enabled" /> Search configured provider within permitted domains</label>
        <label className="checkbox-field"><input type="checkbox" name="demo_mode" /> Use explicit demo/mock crawl (no network)</label>
        <small>Search is reported as unavailable unless the backend search provider is configured. The backend never claims search occurred when it did not.</small>
        <div className="field"><label htmlFor="source_type">Source type</label><select id="source_type" name="source_type" defaultValue="public_profile"><option value="public_profile">Public profile</option><option value="portfolio">Portfolio</option><option value="code_repository">Code repository</option><option value="research_publication">Research publication</option><option value="university_page">University page</option><option value="other">Other</option></select></div>
        <div className="field"><label htmlFor="source_access">Access status</label><select id="source_access" name="source_access" defaultValue="public"><option value="public">Public</option><option value="authorized">Authorized</option></select></div>
        <div className="field"><label htmlFor="domains">Permitted domains <span className="required">*</span></label><input id="domains" name="domains" required placeholder="example.edu, github.com" /><small>The server's allowed-domain policy must also permit each domain.</small></div>
        <div className="scope-status"><span className="live-dot" /> Public / authorized scope only</div>
        <button className="button button-primary button-wide" disabled={busy} type="submit">{busy ? <><span className="spinner small" /> Starting workflow…</> : <>Find candidates <span aria-hidden="true">→</span></>}</button>
        <p className="form-footnote">Submissions create a persisted backend workflow. Candidate data and evidence are returned only by the API.</p>
      </section></aside>
    </form>
  </>
}
function TextField({ label, name, placeholder }: { label: string; name: string; placeholder: string }) { return <div className="field"><label htmlFor={name}>{label}</label><input id={name} name={name} placeholder={placeholder} /></div> }
