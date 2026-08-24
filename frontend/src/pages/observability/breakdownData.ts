import type { BreakdownDimension, UsageBreakdownRow } from '@/hooks/useUsage'
import { dimensionValueLabel } from './labels'

export interface BreakdownDatum {
  key: string
  label: string
  cost: number
  requests: number
}

export const BREAKDOWN_TOP_N = 8

/** Rows arrive cost-descending from the backend, so a plain slice is already
 *  the top N; the remainder folds into one neutral "Other" bar so the chart
 *  still totals the full spend. */
export function topNWithOther(
  rows: UsageBreakdownRow[],
  dimension: BreakdownDimension,
  limit = BREAKDOWN_TOP_N,
): BreakdownDatum[] {
  const mapped: BreakdownDatum[] = rows.map((r) => {
    const value = r[dimension] ?? ''
    return {
      key: value || '__none__',
      label: dimensionValueLabel(dimension, value),
      cost: r.cost_usd,
      requests: r.request_count,
    }
  })
  if (mapped.length <= limit) return mapped
  const tail = mapped.slice(limit)
  return [
    ...mapped.slice(0, limit),
    {
      key: '__other__',
      label: `Other (${tail.length})`,
      cost: tail.reduce((s, r) => s + r.cost, 0),
      requests: tail.reduce((s, r) => s + r.requests, 0),
    },
  ]
}

export function sumCost(data: BreakdownDatum[]): number {
  return data.reduce((s, d) => s + d.cost, 0)
}
