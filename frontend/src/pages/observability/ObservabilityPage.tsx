import { AlertCircle, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useUsageFilterState } from '@/hooks/useUsageFilterState'
import { useSerpApiCredits, useSerperUsage, useUsageDedupe, useUsageSummary } from '@/hooks/useUsage'
import { KpiTiles } from './KpiTiles'

function InlineError({ message }: { message: string }) {
  return (
    <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
      <AlertCircle className="h-4 w-4 shrink-0" />
      {message}
    </div>
  )
}

function ObservabilityPage() {
  const { filters, rangeError } = useUsageFilterState()

  const summary = useUsageSummary(filters)
  const dedupe = useUsageDedupe(filters)
  const serpapi = useSerpApiCredits(24)
  const serper = useSerperUsage()

  const reloadAll = () => {
    summary.reload()
    dedupe.reload()
    serpapi.reload()
    serper.reload()
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
    </div>
  )
}

export { ObservabilityPage, InlineError }
