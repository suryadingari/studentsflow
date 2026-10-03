export function Loading({ label = 'Loading…' }: { label?: string }) {
  return <div className="state-card" role="status"><span className="spinner" aria-hidden="true" />{label}</div>
}
export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return <div className="state-card error-state" role="alert"><span>{error}</span>{onRetry && <button className="button button-secondary" onClick={onRetry}>Try again</button>}</div>
}
export function Empty({ title, children }: { title: string; children?: string }) {
  return <div className="empty-state"><span className="empty-mark" aria-hidden="true">—</span><strong>{title}</strong>{children && <p>{children}</p>}</div>
}
export function StatusBadge({ value }: { value: string }) {
  const kind = value.toLowerCase().replaceAll('_', '-')
  return <span className={`badge badge-${kind}`}>{value.replaceAll('_', ' ')}</span>
}
