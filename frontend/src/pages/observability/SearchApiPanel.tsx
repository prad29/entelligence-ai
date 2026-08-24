import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge } from '@/components/ui/Badge'
import { InlineError } from '@/components/ui/InlineError'
import { InlineWarning } from '@/components/ui/InlineWarning'
import { formatInt, formatTimestamp } from '@/lib/format'
import type { SerpApiCreditsResponse, SerpApiSlot, SerperUsageResponse, UsageResource } from '@/hooks/useUsage'

interface SearchApiPanelProps {
  serpapi: UsageResource<SerpApiCreditsResponse>
  serper: UsageResource<SerperUsageResponse>
}

const SERPAPI_COLUMNS: Column<SerpApiSlot>[] = [
  { key: 'slot', header: 'Slot', cell: (s) => formatInt(s.slot) },
  {
    key: 'plan_searches_left',
    header: 'Plan left',
    className: 'text-right tabular-nums',
    cell: (s) => formatInt(s.plan_searches_left),
  },
  {
    key: 'extra_credits',
    header: 'Extra credits',
    className: 'text-right tabular-nums',
    cell: (s) => formatInt(s.extra_credits),
  },
  {
    key: 'total_searches_left',
    header: 'Total left',
    className: 'text-right tabular-nums',
    cell: (s) => formatInt(s.total_searches_left),
  },
  {
    key: 'this_month_usage',
    header: 'This month',
    className: 'text-right tabular-nums',
    cell: (s) => formatInt(s.this_month_usage),
  },
  { key: 'account_email', header: 'Account', cell: (s) => s.account_email ?? '—' },
  { key: 'as_of', header: 'As of', cell: (s) => formatTimestamp(s.as_of) },
  {
    key: 'error',
    header: 'Status',
    cell: (s) =>
      s.error ? (
        <Badge variant="danger">{s.error}</Badge>
      ) : (
        <Badge variant="success">OK</Badge>
      ),
  },
]

/** SerpApi slot table + Serper definition-list card. Kept as two independent
 *  cards rather than one combined widget — the two providers have unrelated
 *  failure modes (per-key poll errors vs. an unconfigured quota setting) and
 *  merging them would blur which caveat applies to which number.
 *
 *  The optional history line chart (design doc §6.1) is deliberately
 *  skipped: `history` is a flat {ts, slot, total_searches_left} stream
 *  across up to 13 key slots, and a pivoted single-line "pool total over
 *  time" chart didn't earn its keep over the slot table below, which already
 *  carries the operational signal (which key, how much left, whether it
 *  errored). */
function SearchApiPanel({ serpapi, serper }: SearchApiPanelProps) {
  const slots = serpapi.data?.slots ?? []
  const totalSearchesLeft = serpapi.data?.total_searches_left ?? null

  const serperData = serper.data

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>SerpApi credits</CardTitle>
          <CardDescription>
            {formatInt(totalSearchesLeft)} searches left across {slots.length} key
            {slots.length === 1 ? '' : 's'} — sums only slots that reported a number, so a
            partially-failed poll under-reports the true total.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {serpapi.error && <InlineError message={serpapi.error} />}
          <DataTable
            columns={SERPAPI_COLUMNS}
            data={slots}
            keyExtractor={(s) => String(s.slot)}
            emptyMessage={
              serpapi.loading
                ? 'Loading…'
                : 'No credit snapshots yet — the hourly SerpApi poll may not have run.'
            }
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Serper usage</CardTitle>
          <CardDescription>Search calls against the configured monthly quota.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {serper.error && <InlineError message={serper.error} />}
          {!serper.error && (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div className="flex flex-col gap-0.5">
                <dt className="text-xs text-zinc-500 dark:text-zinc-400">Used</dt>
                <dd className="tabular-nums font-medium text-zinc-900 dark:text-zinc-50">
                  {formatInt(serperData?.used)}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-xs text-zinc-500 dark:text-zinc-400">Remaining</dt>
                <dd className="tabular-nums font-medium text-zinc-900 dark:text-zinc-50">
                  {serperData?.quota_configured ? formatInt(serperData.remaining) : '—'}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-xs text-zinc-500 dark:text-zinc-400">Quota total</dt>
                <dd className="tabular-nums font-medium text-zinc-900 dark:text-zinc-50">
                  {serperData?.quota_configured ? formatInt(serperData.quota_total) : '—'}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-xs text-zinc-500 dark:text-zinc-400">Quota period start</dt>
                <dd className="tabular-nums font-medium text-zinc-900 dark:text-zinc-50">
                  {formatTimestamp(serperData?.quota_period_start)}
                </dd>
              </div>
            </dl>
          )}

          {!serper.error && serperData && !serperData.quota_configured && serperData.warning && (
            <InlineWarning message={serperData.warning} />
          )}

          <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
            Serper publishes no remaining-credits API. Remaining is <code>SERPER_QUOTA_TOTAL</code> minus
            recorded calls and will drift if the plan is topped up without updating that setting.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

export { SearchApiPanel }
