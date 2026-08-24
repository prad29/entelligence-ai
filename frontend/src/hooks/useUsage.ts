import { useEffect, useState } from 'react'
import api from '@/lib/api'

// ---- filter state -----------------------------------------------------------

export interface UsageFilterState {
  start: string      // YYYY-MM-DD (UTC)
  end: string        // YYYY-MM-DD (UTC), inclusive of that whole day server-side
  taskType: string   // '' = all
  modelId: string
  callerType: string
  apiKeyId: string
  market: string
}

/** Maps camelCase UI state onto the backend's snake_case query params,
 *  omitting empties so "all" is expressed by absence, not by a sentinel. */
export function buildUsageParams(f: UsageFilterState): Record<string, string> {
  const params: Record<string, string> = { start: f.start, end: f.end }
  if (f.taskType) params.task_type = f.taskType
  if (f.modelId) params.model_id = f.modelId
  if (f.callerType) params.caller_type = f.callerType
  if (f.apiKeyId) params.api_key_id = f.apiKeyId
  if (f.market) params.market = f.market
  return params
}

// ---- response types (exact backend shapes) ----------------------------------

export interface UsageSums {
  request_count: number
  cache_hit_count: number
  failure_count: number
  retry_count_sum: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_usd: number
  latency_ms_sum: number
}

export interface UsageRange { start: string; end: string }

export interface UsageSummaryResponse {
  range: UsageRange
  totals: UsageSums
  derived: {
    avg_latency_ms: number | null
    failure_rate: number | null
    cache_hit_rate: number | null
    cost_per_request: number | null
  }
}

export type Granularity = 'hour' | 'day'
export interface UsageTimeseriesPoint extends UsageSums { bucket: string }
export interface UsageTimeseriesResponse {
  range: UsageRange
  granularity: Granularity
  points: UsageTimeseriesPoint[]
}

export type BreakdownDimension = 'task_type' | 'model_id' | 'caller_type' | 'api_key_id' | 'market'
/** Rows carry the grouped dimension's own key plus every sum field. The
 *  dimension value can be '' — the rollup table stores '' where the raw log
 *  stores NULL for api_key_id/market. */
export type UsageBreakdownRow = UsageSums & Partial<Record<BreakdownDimension, string>>
export interface UsageBreakdownResponse {
  range: UsageRange
  dimension: BreakdownDimension
  rows: UsageBreakdownRow[]
}

export interface DedupeTaskRow {
  task_type: string
  attempted: number
  cache_hits: number
  dedupe_rate: number | null
  estimated_savings_usd: number
}
export interface UsageDedupeResponse {
  range: UsageRange
  by_task_type: DedupeTaskRow[]
  overall: { attempted: number; cache_hits: number; dedupe_rate: number | null; estimated_savings_usd: number }
}

export interface SerpApiSlot {
  slot: number
  plan_searches_left: number | null
  extra_credits: number | null
  total_searches_left: number | null
  this_month_usage: number | null
  account_email: string | null
  error: string | null
  as_of: string
}
export interface SerpApiCreditsResponse {
  slots: SerpApiSlot[]
  total_searches_left: number | null
  history: { ts: string; slot: number; total_searches_left: number | null }[]
  history_hours: number
}

export interface SerperUsageResponse {
  quota_configured: boolean
  quota_total: number | null
  quota_period_start: string | null
  used: number
  remaining: number | null
  warning: string | null
}

export type ReportFormat = 'csv' | 'pdf'

// ---- generic fetch hook -----------------------------------------------------

export interface UsageResource<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

/** One fetch-on-params-change hook, matching the repo's plain
 *  useState/useEffect + cancelled-flag pattern (no React Query here).
 *
 *  `params` is serialised into the dep array rather than listed directly:
 *  callers build it inline (`{ ...buildUsageParams(f), dimension }`), so the
 *  object identity changes every render and would loop forever. */
function useUsageResource<T>(path: string, params: Record<string, string>): UsageResource<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const paramKey = JSON.stringify(params)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .get<T>(path, { params: JSON.parse(paramKey) as Record<string, string> })
      .then((res) => { if (!cancelled) setData(res.data) })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Request failed')
        setData(null)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [path, paramKey, reloadKey])

  return { data, loading, error, reload: () => setReloadKey((k) => k + 1) }
}

// ---- endpoint wrappers ------------------------------------------------------

export function useUsageSummary(f: UsageFilterState) {
  return useUsageResource<UsageSummaryResponse>('/api/v1/usage/summary', buildUsageParams(f))
}

export function useUsageTimeseries(f: UsageFilterState, granularity: Granularity) {
  return useUsageResource<UsageTimeseriesResponse>(
    '/api/v1/usage/timeseries', { ...buildUsageParams(f), granularity },
  )
}

export function useUsageBreakdown(dimension: BreakdownDimension, f: UsageFilterState) {
  return useUsageResource<UsageBreakdownResponse>(
    '/api/v1/usage/breakdown', { ...buildUsageParams(f), dimension },
  )
}

export function useUsageDedupe(f: UsageFilterState) {
  return useUsageResource<UsageDedupeResponse>('/api/v1/usage/dedupe', buildUsageParams(f))
}

/** Takes no UsageFilters by design: credits are a property of the key pool
 *  right now, not of an LLM-call date range (routers/usage.py:181). */
export function useSerpApiCredits(historyHours = 24) {
  return useUsageResource<SerpApiCreditsResponse>(
    '/api/v1/usage/serpapi-credits', { history_hours: String(historyHours) },
  )
}

export function useSerperUsage() {
  return useUsageResource<SerperUsageResponse>('/api/v1/usage/serper-usage', {})
}

// ---- report download -------------------------------------------------------

export function useUsageReport() {
  const [downloading, setDownloading] = useState<ReportFormat | null>(null)
  const [error, setError] = useState<string | null>(null)

  const download = async (format: ReportFormat, f: UsageFilterState) => {
    setDownloading(format)
    setError(null)
    try {
      const res = await api.get('/api/v1/usage/report', {
        params: { ...buildUsageParams(f), format },
        responseType: 'blob',
      })
      const disposition = String(res.headers['content-disposition'] ?? '')
      const match = /filename="?([^";]+)"?/.exec(disposition)
      const filename = match?.[1] ?? `usage-report-${f.start}-to-${f.end}.${format}`
      const url = URL.createObjectURL(res.data as Blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Report download failed')
    } finally {
      setDownloading(null)
    }
  }

  return { downloading, error, download }
}

// Two caveats worth keeping close to the code that hits them:
// - With `responseType: 'blob'`, the axios interceptor in `lib/api.ts` cannot
//   read `error.response.data.detail` (the body is a `Blob`), so a 400
//   surfaces as the generic "Request failed with status code 400". That's
//   acceptable because the filter bar already prevents the only two
//   reachable 400s (inverted range, >366-day span); do not try to un-blob
//   the error.
// - `api` sets a default `Content-Type: application/json` header on GETs.
//   Harmless here.
