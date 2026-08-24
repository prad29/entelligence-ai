import { useRef, useState } from 'react'
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
  type BreakdownDimension,
  type Granularity,
  type UsageFilterState,
} from '@/hooks/useUsage'
import { KpiTiles } from './KpiTiles'
import { UsageTimeSeriesChart, type TimeseriesMetric } from './UsageTimeSeriesChart'
import { BreakdownDonutChart } from './BreakdownDonutChart'
import { BreakdownBarChart } from './BreakdownBarChart'
import { UsageFilterBar } from './UsageFilterBar'
import { useUsageFilterOptions } from './useUsageFilterOptions'
import { UsageDetailTable } from './UsageDetailTable'
import { ReportDownloadButtons } from './ReportDownloadButtons'

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
  const { filters, setFilters, resetFilters, rangeError } = useUsageFilterState()

  // Freeze the last range/filters that passed client-side validation so an
  // in-progress invalid edit (e.g. start > end while the user is still
  // typing) never fires a request — the filter bar itself stays bound to
  // the live `filters` so the date inputs and inline error reflect exactly
  // what the user typed. A ref write during render is safe here because
  // it's an idempotent cache of the last good value.
  const lastValid = useRef(filters)
  if (!rangeError) lastValid.current = filters
  const queryFilters = rangeError ? lastValid.current : filters

  const summary = useUsageSummary(queryFilters)
  const dedupe = useUsageDedupe(queryFilters)
  const serpapi = useSerpApiCredits(24)
  const serper = useSerperUsage()
  const { modelOptions, apiKeyOptions } = useUsageFilterOptions(queryFilters)

  const [metric, setMetric] = useState<TimeseriesMetric>('cost')
  const [granularity, setGranularity] = useState<Granularity>(
    () => (rangeSpanDays(filters) <= 3 ? 'hour' : 'day'),
  )

  const timeseries = useUsageTimeseries(queryFilters, granularity)
  const byTaskType = useUsageBreakdown('task_type', queryFilters)
  const byCallerType = useUsageBreakdown('caller_type', queryFilters)
  const byModel = useUsageBreakdown('model_id', queryFilters)

  const [tableDimension, setTableDimension] = useState<BreakdownDimension>('task_type')
  // Fetched independently of byTaskType/byCallerType/byModel above — when
  // tableDimension === 'task_type' this duplicates the donut's request.
  // That's a cheap aggregate query; the duplication keeps each component's
  // data flow independent, so no request cache is added for it.
  const detail = useUsageBreakdown(tableDimension, queryFilters)

  const reloadAll = () => {
    summary.reload()
    dedupe.reload()
    serpapi.reload()
    serper.reload()
    timeseries.reload()
    byTaskType.reload()
    byCallerType.reload()
    byModel.reload()
    detail.reload()
  }

  return (
    <div className="flex flex-col gap-6">
      <UsageFilterBar
        filters={filters}
        onChange={setFilters}
        onReset={resetFilters}
        rangeError={rangeError}
        modelOptions={modelOptions}
        apiKeyOptions={apiKeyOptions}
        actions={
          <div className="flex items-center gap-2">
            <ReportDownloadButtons filters={queryFilters} disabled={!!rangeError} />
            <Button variant="secondary" size="sm" onClick={reloadAll}>
              <RotateCcw className="h-3.5 w-3.5" />
              Refresh
            </Button>
          </div>
        }
      />

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

      <UsageDetailTable
        dimension={tableDimension}
        onDimensionChange={setTableDimension}
        resource={detail}
      />
    </div>
  )
}

export { ObservabilityPage, InlineError }
