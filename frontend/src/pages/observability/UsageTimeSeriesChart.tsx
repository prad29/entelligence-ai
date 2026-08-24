import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { cn } from '@/lib/utils'
import type { Granularity, UsageResource, UsageTimeseriesResponse } from '@/hooks/useUsage'
import { CHART_AXIS_LINE, CHART_GRID, CHART_SERIES, CHART_TICK } from '@/lib/chartTheme'
import { formatBucketLabel, formatCompact, formatInt, formatUsd } from '@/lib/format'
import { ChartCard } from './ChartCard'
import { ChartTooltip } from '@/components/ui/ChartTooltip'
import { ChartLegend } from '@/components/ui/ChartLegend'

export type TimeseriesMetric = 'cost' | 'requests' | 'tokens'

interface UsageTimeSeriesChartProps {
  resource: UsageResource<UsageTimeseriesResponse>
  metric: TimeseriesMetric
  onMetricChange: (m: TimeseriesMetric) => void
  granularity: Granularity
  onGranularityChange: (g: Granularity) => void
}

/** Small segmented button group reusing RegionToggle's exact pill styling.
 *  Phase 5's table reuses `Tabs` instead, so this stays local rather than a
 *  shared primitive. */
interface SegmentedOption<T extends string> {
  value: T
  label: string
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: SegmentedOption<T>[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex h-9 items-center gap-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-800/60 p-1">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={cn(
            'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
            'text-zinc-600 dark:text-zinc-400',
            'hover:text-zinc-900 dark:hover:text-zinc-100',
            value === opt.value && 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4A9FD4]/50',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

const METRIC_OPTIONS: SegmentedOption<TimeseriesMetric>[] = [
  { value: 'cost', label: 'Cost' },
  { value: 'requests', label: 'Requests' },
  { value: 'tokens', label: 'Tokens' },
]

const GRANULARITY_OPTIONS: SegmentedOption<Granularity>[] = [
  { value: 'hour', label: 'Hour' },
  { value: 'day', label: 'Day' },
]

/** One metric at a time (with a switcher) rather than cost + requests on
 *  twin Y axes, because dual axes let the reader infer a correlation that
 *  the arbitrary axis scaling invented. Tokens is the only stacked case,
 *  and it is a genuine part-of-whole (input + output = total). */
function UsageTimeSeriesChart({
  resource,
  metric,
  onMetricChange,
  granularity,
  onGranularityChange,
}: UsageTimeSeriesChartProps) {
  // Buckets are sparse — deliberately do NOT zero-fill. A category X axis
  // over present buckets is honest about "no calls happened"; a synthetic
  // zero-filled series would imply we recorded a zero.
  const chartData = useMemo(
    () =>
      (resource.data?.points ?? []).map((p) => ({
        bucket: p.bucket,
        label: formatBucketLabel(p.bucket, granularity),
        cost: p.cost_usd,
        requests: p.request_count,
        inputTokens: p.input_tokens,
        outputTokens: p.output_tokens,
      })),
    [resource.data, granularity],
  )

  const legendItems =
    metric === 'tokens'
      ? [
          { label: 'Input tokens', color: CHART_SERIES.inputTokens },
          { label: 'Output tokens', color: CHART_SERIES.outputTokens },
        ]
      : metric === 'cost'
        ? [{ label: 'Cost', color: CHART_SERIES.cost }]
        : [{ label: 'Requests', color: CHART_SERIES.requests }]

  return (
    <ChartCard
      title="Usage over time"
      description="Cost, requests, or token volume across the selected range."
      actions={
        <div className="flex items-center gap-2">
          <Segmented value={metric} options={METRIC_OPTIONS} onChange={onMetricChange} />
          <Segmented value={granularity} options={GRANULARITY_OPTIONS} onChange={onGranularityChange} />
        </div>
      }
      legend={<ChartLegend items={legendItems} />}
      loading={resource.loading}
      error={resource.error}
      empty={!resource.loading && !resource.error && chartData.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={CHART_TICK}
            tickLine={false}
            axisLine={{ stroke: CHART_AXIS_LINE }}
            interval="preserveStartEnd"
            minTickGap={28}
          />
          <YAxis
            tick={CHART_TICK}
            tickLine={false}
            axisLine={false}
            width={64}
            tickFormatter={(v: number) => (metric === 'cost' ? formatUsd(v) : formatCompact(v))}
          />
          <Tooltip
            cursor={{ stroke: CHART_AXIS_LINE }}
            content={
              <ChartTooltip
                formatValue={(v, key) => (key === 'cost' ? formatUsd(v) : formatInt(v))}
              />
            }
          />
          {metric === 'cost' && (
            <Area
              type="monotone"
              dataKey="cost"
              name="Cost"
              stroke={CHART_SERIES.cost}
              strokeWidth={2}
              fill={CHART_SERIES.cost}
              fillOpacity={0.14}
              dot={false}
              activeDot={{ r: 3 }}
            />
          )}
          {metric === 'requests' && (
            <Area
              type="monotone"
              dataKey="requests"
              name="Requests"
              stroke={CHART_SERIES.requests}
              strokeWidth={2}
              fill={CHART_SERIES.requests}
              fillOpacity={0.14}
              dot={false}
              activeDot={{ r: 3 }}
            />
          )}
          {metric === 'tokens' && (
            <>
              <Area
                type="monotone"
                stackId="tokens"
                dataKey="inputTokens"
                name="Input tokens"
                stroke={CHART_SERIES.inputTokens}
                strokeWidth={2}
                fill={CHART_SERIES.inputTokens}
                fillOpacity={0.18}
                dot={false}
              />
              <Area
                type="monotone"
                stackId="tokens"
                dataKey="outputTokens"
                name="Output tokens"
                stroke={CHART_SERIES.outputTokens}
                strokeWidth={2}
                fill={CHART_SERIES.outputTokens}
                fillOpacity={0.18}
                dot={false}
              />
            </>
          )}
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

export { UsageTimeSeriesChart }
