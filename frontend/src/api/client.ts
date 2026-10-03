import type { Kpis, PersistedStep, Workflow, WorkflowRequest } from '../types/api'

const baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') || 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
  } catch {
    throw new Error('Unable to reach FastAPI. Check that the backend is running and the API URL is configured.')
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body: unknown = await response.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        const detail: unknown = body.detail
        if (typeof detail === 'string') message = detail.replace(/^['"](.+)['"]$/, '$1')
        else if (Array.isArray(detail)) {
          const issues = detail.flatMap((item: unknown) => {
            if (typeof item !== 'object' || item === null) return []
            const entry = item as { loc?: unknown; msg?: unknown }
            if (typeof entry.msg !== 'string') return []
            const location = Array.isArray(entry.loc) ? entry.loc.filter((part): part is string | number => typeof part === 'string' || typeof part === 'number').slice(1).join('.') : ''
            return [`${location ? `${location}: ` : ''}${entry.msg}`]
          })
          if (issues.length) message = `Please check the submitted fields. ${issues.join(' · ')}`
          else if (response.status === 422) message = 'The request did not match the API requirements. Please check the submitted fields.'
        }
      } else if (response.status === 422) {
        message = 'The request did not match the API requirements. Please check the submitted fields.'
      }
    } catch { /* Keep the concise status message when the response is not JSON. */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  workflows: (limit = 50) => request<Workflow[]>(`/workflows?limit=${limit}`),
  workflow: (id: string) => request<Workflow>(`/workflows/${encodeURIComponent(id)}`),
  steps: (id: string) => request<PersistedStep[]>(`/workflows/${encodeURIComponent(id)}/steps`),
  kpis: () => request<Kpis>('/workflows/observability/kpis'),
  createWorkflow: (payload: WorkflowRequest) => request<Workflow>('/workflows', { method: 'POST', body: JSON.stringify(payload) }),
  approval: (workflowId: string, draftId: string, action: 'approve' | 'reject' | 'edit', details: { actor_id: string; subject?: string; body?: string; reason?: string }) =>
    request<Workflow>(`/workflows/${encodeURIComponent(workflowId)}/approvals`, {
      method: 'POST', body: JSON.stringify({ draft_id: draftId, action: { action, ...details } }),
    }),
}

export function getApiBaseUrl(): string { return baseUrl }
