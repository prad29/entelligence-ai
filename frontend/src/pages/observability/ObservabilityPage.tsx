import { useState } from 'react'
import { AlertCircle, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useUsageFilterState } from '@/hooks/useUsageFilterState'
import {
  useSerpApiCredits,
  useSerperUsage,
  useUsageBreakdown,
  useUsageDedupe,
  useUsageSummary,
  useUsageTimeseries,
  type Granularity,
  type UsageFilterState,
} from '@/hooks/useUsage'
import { KpiTiles } from './KpiTiles'
import { UsageTimeSeriesChart, type TimeseriesMetric } from './UsageTimeSeriesChart'
import { BreakdownDonutChart } from './BreakdownDonutChart'
import { BreakdownBarChart } from './BreakdownBarChart'

function InlineError({ message }: { message: string }) {
  return (
    <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
      <AlertCircle className="h-4 w-4 shrink-0" />
      {message}
    </div>
  )
}

/** Whole-day span, inclusive of both endpoints — matches the backend's
 *  widening of a bare `end` date to the whole day (routers/usage.py). Used
 *  only to pick a sensible initial granularity. */
function rangeSpanDays(filters: UsageFilterState): number {
  const start = Date.parse(`${filters.start}T00:00:00Z`)
  const end = Date.parse(`${filters.end}T00:00:00Z`)
  if (Number.isNaN(start) || Number.isNaN(end)) return DEFAULT_SPAN_DAYS
  return (end - start) / 86_400_000 + 1
}

const DEFAULT_SPAN_DAYS = 7

function ObservabilityPage() {
  const { filters, rangeError } = useUsageFilterState()

  const summary = useUsageSummary(filters)
  const dedupe = useUsageDedupe(filters)
  const serpapi = useSerpApiCredits(24)
  const serper = useSerperUsage()

  const [metric, setMetric] = useState<TimeseriesMetric>('cost')
  const [granularity, setGranularity] = useState<Granularity>(
    () => (rangeSpanDays(filters) <= 3 ? 'hour' : 'day'),
  )

  const timeseries = useUsageTimeseries(filters, granularity)
  const byTaskType = useUsageBreakdown('task_type', filters)
  const byCallerType = useUsageBreakdown('caller_type', filters)
  const byModel = useUsageBreakdown('model_id', filters)

  const reloadAll = () => {
    summary.reload()
    dedupe.reload()
    serpapi.reload()
    serper.reload()
    timeseries.reload()
    byTaskType.reload()
    byCallerType.reload()
    byModel.reload()
  }

  return (
    <div className="flex flex-col gap-6">
      {/* header row: range caption on the left, Refresh on the right.
          Phase 4 replaces the caption with <UsageFilterBar/>; Phase 5 adds
          the report buttons next to Refresh. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
            {filters.start} → {filters.end} (UTC)
          </p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Aggregates refresh hourly; the current partial hour is read live from raw call logs.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={reloadAll}>
          <RotateCcw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {rangeError && <InlineError message={rangeError} />}

      <KpiTiles summary={summary} dedupe={dedupe} serpapi={serpapi} serper={serper} />

      <UsageTimeSeriesChart
        resource={timeseries}
        metric={metric}
        onMetricChange={setMetric}
        granularity={granularity}
        onGranularityChange={setGranularity}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <BreakdownDonutChart
          title="Cost by task type"
          description="Share of spend across the four instrumented Bedrock tasks."
          dimension="task_type"
          resource={byTaskType}
        />
        <BreakdownDonutChart
          title="Cost by caller"
          description="Portal is a single unattributed bucket; external API calls carry an API key."
          dimension="caller_type"
          resource={byCallerType}
        />
      </div>

      <BreakdownBarChart
        title="Cost by model"
        description="Top models by spend in this range."
        dimension="model_id"
        resource={byModel}
      />
    </div>
  )
}

export { ObservabilityPage, InlineError }
