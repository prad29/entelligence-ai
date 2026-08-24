import { useMemo } from 'react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge } from '@/components/ui/Badge'
import { formatUsd, formatInt, formatCompact, formatMs } from '@/lib/format'
import type { BreakdownDimension, UsageBreakdownResponse, UsageResource } from '@/hooks/useUsage'
import { DIMENSION_LABELS, dimensionValueLabel } from './labels'
import { InlineError } from '@/components/ui/InlineError'

interface UsageDetailTableProps {
  dimension: BreakdownDimension
  onDimensionChange: (d: BreakdownDimension) => void
  resource: UsageResource<UsageBreakdownResponse>
}

const BREAKDOWN_DIMENSIONS: BreakdownDimension[] = [
  'task_type',
  'model_id',
  'caller_type',
  'api_key_id',
  'market',
]

interface DetailRow {
  id: string
  label: string
  requests: number
  cacheHits: number
  failures: number
  retries: number
  inputTokens: number
  outputTokens: number
  cost: number
  costPerRequest: number | null
  avgLatencyMs: number | null
}

function UsageDetailTable({ dimension, onDimensionChange, resource }: UsageDetailTableProps) {
  const tabs = useMemo(
    () => BREAKDOWN_DIMENSIONS.map((d) => ({ value: d, label: DIMENSION_LABELS[d] })),
    [],
  )

  const rows: DetailRow[] = useMemo(
    () =>
      (resource.data?.rows ?? []).map((r, i) => {
        const value = r[dimension] ?? ''
        return {
          id: `${value || '__none__'}-${i}`,
          label: dimensionValueLabel(dimension, value),
          requests: r.request_count,
          cacheHits: r.cache_hit_count,
          failures: r.failure_count,
          retries: r.retry_count_sum,
          inputTokens: r.input_tokens,
          outputTokens: r.output_tokens,
          cost: r.cost_usd,
          costPerRequest: r.request_count ? r.cost_usd / r.request_count : null,
          avgLatencyMs: r.request_count ? r.latency_ms_sum / r.request_count : null,
        }
      }),
    [resource.data, dimension],
  )

  // Only `label` is sortable. DataTable's built-in sort is
  // `String(a).localeCompare(String(b))` (DataTable.tsx:53), a lexicographic
  // string comparator that would order "1000" before "9" on any numeric
  // column. The backend already returns rows cost-descending, which is the
  // ordering that matters here — do not add `sortable: true` to a numeric
  // column.
  const columns: Column<DetailRow>[] = [
    {
      key: 'label',
      header: DIMENSION_LABELS[dimension],
      sortable: true,
      cell: (row) => row.label,
    },
    {
      key: 'cost',
      header: 'Cost',
      className: 'text-right tabular-nums',
      cell: (row) => formatUsd(row.cost),
    },
    {
      key: 'costPerRequest',
      header: '$/req',
      className: 'text-right tabular-nums',
      cell: (row) => formatUsd(row.costPerRequest),
    },
    {
      key: 'requests',
      header: 'Requests',
      className: 'text-right tabular-nums',
      cell: (row) => formatInt(row.requests),
    },
    {
      key: 'cacheHits',
      header: 'Cache hits',
      className: 'text-right tabular-nums',
      cell: (row) => formatInt(row.cacheHits),
    },
    {
      key: 'failures',
      header: 'Failures',
      className: 'text-right tabular-nums',
      cell: (row) =>
        row.failures > 0 ? (
          <Badge variant="danger">{formatInt(row.failures)}</Badge>
        ) : (
          formatInt(row.failures)
        ),
    },
    {
      key: 'inputTokens',
      header: 'Input tok',
      className: 'text-right tabular-nums',
      cell: (row) => formatCompact(row.inputTokens),
    },
    {
      key: 'outputTokens',
      header: 'Output tok',
      className: 'text-right tabular-nums',
      cell: (row) => formatCompact(row.outputTokens),
    },
    {
      key: 'avgLatencyMs',
      header: 'Avg latency',
      className: 'text-right tabular-nums',
      cell: (row) => formatMs(row.avgLatencyMs),
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Detail</CardTitle>
        <CardDescription>Grouped totals for the current filters, highest cost first.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {resource.error && <InlineError message={resource.error} />}
        <Tabs
          tabs={tabs}
          value={dimension}
          onValueChange={(v) => onDimensionChange(v as BreakdownDimension)}
        >
          <TabsContent value={dimension}>
            <DataTable
              columns={columns}
              data={rows}
              keyExtractor={(row) => row.id}
              emptyMessage={resource.loading ? 'Loading…' : 'No usage recorded in this range.'}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

export { UsageDetailTable }
