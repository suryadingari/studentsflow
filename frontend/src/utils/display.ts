export function format(value: number): string { return new Intl.NumberFormat().format(value) }
export function shortId(value: string): string { return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-5)}` : value }
export function date(value: string): string { const time = Date.parse(value); return Number.isNaN(time) ? '—' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(time) }
