import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { UsageFilterState } from './useUsage'

/** UTC, because every backend timestamp is naive UTC — using local date parts
 *  would shift the whole range by the browser's offset. */
function toUtcDateInput(d: Date): string { return d.toISOString().slice(0, 10) }

/** `days` is inclusive of today: presetRange(7) === today-6 .. today. */
export function presetRange(days: number): { start: string; end: string } {
  const now = new Date()
  return {
    start: toUtcDateInput(new Date(now.getTime() - (days - 1) * 86_400_000)),
    end: toUtcDateInput(now),
  }
}

export const DEFAULT_RANGE_DAYS = 7
/** Never hoist this to a module constant — a tab left open across midnight
 *  would keep querying yesterday's range. */
function defaultRange() { return presetRange(DEFAULT_RANGE_DAYS) }

export function useUsageFilterState() {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters: UsageFilterState = useMemo(() => {
    const fallback = defaultRange()
    return {
      start: searchParams.get('start') || fallback.start,
      end: searchParams.get('end') || fallback.end,
      taskType: searchParams.get('task_type') ?? '',
      modelId: searchParams.get('model_id') ?? '',
      callerType: searchParams.get('caller_type') ?? '',
      apiKeyId: searchParams.get('api_key_id') ?? '',
      market: searchParams.get('market') ?? '',
    }
  }, [searchParams])

  const setFilters = useCallback((patch: Partial<UsageFilterState>) => {
    const next = { ...filters, ...patch }
    const sp = new URLSearchParams()
    sp.set('start', next.start)
    sp.set('end', next.end)
    if (next.taskType) sp.set('task_type', next.taskType)
    if (next.modelId) sp.set('model_id', next.modelId)
    if (next.callerType) sp.set('caller_type', next.callerType)
    if (next.apiKeyId) sp.set('api_key_id', next.apiKeyId)
    if (next.market) sp.set('market', next.market)
    setSearchParams(sp, { replace: true })
  }, [filters, setSearchParams])

  const resetFilters = useCallback(() => {
    const r = defaultRange()
    setSearchParams(new URLSearchParams({ start: r.start, end: r.end }), { replace: true })
  }, [setSearchParams])

  /** Client-side mirror of the backend's two 400s so the user sees the
   *  problem before a request is fired (routers/usage.py:111-126). */
  const rangeError = useMemo(() => {
    if (!filters.start || !filters.end) return null
    if (filters.start > filters.end) return 'Start date must be on or before the end date.'
    const spanDays = (Date.parse(`${filters.end}T00:00:00Z`) - Date.parse(`${filters.start}T00:00:00Z`)) / 86_400_000 + 1
    if (spanDays > 366) return 'Range may not exceed 366 days.'
    return null
  }, [filters.start, filters.end])

  return { filters, setFilters, resetFilters, rangeError }
}
