const EM_DASH = '—'

/** Cost. LLM costs span 4 orders of magnitude, so precision is adaptive:
 *  sub-dollar values keep 4dp (a single call is often $0.0031), everything
 *  else gets 2dp, and anything over 10k is compacted. */
export function formatUsd(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return EM_DASH
  const abs = Math.abs(value)
  if (abs === 0) return '$0.00'
  if (abs < 1) return `$${value.toFixed(4)}`
  if (abs < 10_000) return `$${value.toFixed(2)}`
  return `$${new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)}`
}

export function formatInt(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return EM_DASH
  return new Intl.NumberFormat('en-US').format(Math.round(value))
}

export function formatCompact(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return EM_DASH
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function formatPct(rate?: number | null, digits = 1): string {
  if (rate == null || Number.isNaN(rate)) return EM_DASH
  return `${(rate * 100).toFixed(digits)}%`
}

export function formatMs(ms?: number | null): string {
  if (ms == null || Number.isNaN(ms)) return EM_DASH
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

/** Backend buckets are naive-UTC ISO strings with no offset. Appending 'Z'
 *  before parsing stops the browser reading them as local time and shifting
 *  every point by the UTC offset. */
export function parseUtcIso(iso: string): Date {
  return new Date(/[Z+]|-\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
}

export function formatBucketLabel(iso: string, granularity: 'hour' | 'day'): string {
  const d = parseUtcIso(iso)
  return granularity === 'hour'
    ? d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', hour12: false, timeZone: 'UTC' })
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

export function formatTimestamp(iso?: string | null): string {
  if (!iso) return EM_DASH
  return parseUtcIso(iso).toLocaleString('en-US', { timeZone: 'UTC' }) + ' UTC'
}
