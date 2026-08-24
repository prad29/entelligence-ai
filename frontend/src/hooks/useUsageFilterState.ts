import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { UsageFilterState } from './useUsage'

/** The dashboard's date range is selected in IST, not the browser's local
 *  time or the backend's storage timezone (naive UTC). A Date object has no
 *  inherent timezone, so "today in IST" is computed the standard way: shift
 *  the current instant by the IST offset, then read its UTC calendar
 *  fields — those UTC-getter values are now the IST wall-clock date. */
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000

function nowInIst(): Date {
  return new Date(Date.now() + IST_OFFSET_MS)
}

function istDateInput(d: Date): string { return d.toISOString().slice(0, 10) }

export function todayIst(): string { return istDateInput(nowInIst()) }

/** `days` is inclusive of today: presetRange(7) === today-6 .. today, both
 *  IST calendar dates. */
export function presetRange(days: number): { start: string; end: string } {
  const now = nowInIst()
  return {
    start: istDateInput(new Date(now.getTime() - (days - 1) * 86_400_000)),
    end: istDateInput(now),
  }
}

/** Converts an IST calendar-day boundary (YYYY-MM-DD, as selected in the UI)
 *  into the exact instant the backend should treat as that day's start —
 *  expressed with an explicit +05:30 offset so routers/usage.py's
 *  `_parse_dt` normalises it to UTC itself, rather than duplicating that
 *  arithmetic on the client. */
export function istDayStartIso(dateStr: string): string {
  return `${dateStr}T00:00:00+05:30`
}

/** Exclusive end bound: midnight IST at the start of the day *after*
 *  `dateStr`, so the selected end date's full 24h IST day is included
 *  (routers/usage.py's `end` bound is exclusive). */
export function istDayEndIso(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  const next = new Date(Date.UTC(y, m - 1, d + 1))
  return `${next.toISOString().slice(0, 10)}T00:00:00+05:30`
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
