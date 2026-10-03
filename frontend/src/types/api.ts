export type WorkflowStatus = 'created' | 'running' | 'waiting_for_approval' | 'paused' | 'completed' | 'failed' | 'cancelled'
export type StepStatus = 'pending' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'skipped' | 'blocked'

export interface Evidence {
  evidence_id: string
  claim_type: string
  claim_value: unknown
  evidence_type: string
  source_url: string
  final_url?: string | null
  source_title?: string | null
  supporting_text: string
  extracted_at: string
  strength: string
  validation_status: string
  ai_category?: string | null
}
export interface Profile {
  name?: { value: unknown } | null
  university?: { value: unknown } | null
  degree?: { value: unknown } | null
  branch?: { value: unknown } | null
  location?: { value: unknown } | null
  skills: Array<{ value: unknown }>
  projects: Array<{ value: unknown }>
  research_interests: Array<{ value: unknown }>
  final_year_status: string
  ai_interest_status: string
  evidence: Evidence[]
}
export interface ValidatedProfile { profile: Profile; final_year_status: string; ai_interest_status: string }
export interface CanonicalStudent { canonical_id: string; profile: Profile }
export interface Assessment {
  criterion_type: string
  value?: string | null
  required: boolean
  status: string
  explanation: string
  evidence: Evidence[]
  source_urls: string[]
}
export interface CandidateMatch {
  student_reference?: string | null
  canonical_student_id: string
  candidate_name?: string | null
  status: string
  assessments: Assessment[]
  explanation: string
  uncertainties: string[]
}
export interface Draft {
  draft_id: string
  candidate_id: string
  recipient?: { email: string; source_url?: string | null } | null
  subject: string
  body: string
  status: string
  versions: Array<{ version_number: number; subject: string; body: string; authored_by: string; created_at: string }>
  approvals: Array<{ action_id: string; action: string; actor_id: string; occurred_at: string; version_number: number; reason?: string | null }>
}
export interface OutreachResult {
  outreach_id: string
  status: string
  provider_result?: { provider_name: string; timestamp: string; recipient: string } | null
  events: Array<{ event_type: string; occurred_at: string; provider?: string | null }>
}
export interface FollowUpResult { plan?: { original_outreach_id: string; status: string; due_at?: string | null; recipient: string } | null; explanation: string; reply_status: string }
export interface WorkflowStep {
  step_id: string
  workflow_id: string
  step_type: string
  agent_name?: string | null
  agent_version?: string | null
  state: StepStatus
  started_at?: string | null
  ended_at?: string | null
  error?: string | null
  retry_count: number
}
export interface Workflow {
  workflow_id: string
  requirement_id: string
  status: WorkflowStatus
  current_step?: string | null
  steps: WorkflowStep[]
  drafts: Draft[]
  validated_profiles: ValidatedProfile[]
  canonical_students: CanonicalStudent[]
  candidate_matches: CandidateMatch[]
  outreach_results: OutreachResult[]
  outreach_draft_ids: Record<string, string>
  follow_up_results: FollowUpResult[]
  errors: string[]
  created_at: string
  updated_at: string
  metrics: Record<string, number>
}
export interface PersistedStep extends Omit<WorkflowStep, 'workflow_id' | 'step_type'> { step_type: string; input_metadata?: Record<string, unknown>; output_metadata?: Record<string, unknown> }
export type Kpis = Record<string, number>
export interface Criterion {
  criterion_type: 'final_year' | 'ai_interest' | 'location' | 'university' | 'degree' | 'branch' | 'skill' | 'ai_area' | 'project' | 'research' | 'experience'
  value?: string
  required: boolean
}
export interface WorkflowRequest {
  requirement: { raw_text: string; criteria: Criterion[] }
  sources: Array<{ url: string; domain: string; source_type: string; reason: string; access: 'public' | 'authorized' }>
  permitted_domains: string[]
  crawl_configuration: { allowed_domains: string[]; max_pages: number; max_depth: number }
  demo_mode: boolean
}
