import { useEffect, useState } from 'react'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge } from '@/components/ui/Badge'
import { InlineError } from '@/components/ui/InlineError'
import { cn } from '@/lib/utils'
import { formatInt, formatTimestamp } from '@/lib/format'
import type { SerpApiCreditsResponse, SerpApiSlot, UsageResource } from '@/hooks/useUsage'

interface SearchApiPanelProps {
  serpapi: UsageResource<SerpApiCreditsResponse>
}

const SERPAPI_PAGE_SIZE = 4

/** Simple numbered pager (1, 2, 3, …) for a client-side-paginated list.
 *  Local to this file since 13 SerpApi key slots is the only place in the
 *  dashboard with enough rows to need paging. */
function Pager({
  page,
  pageCount,
  onPageChange,
}: {
  page: number
  pageCount: number
  onPageChange: (page: number) => void
}) {
  if (pageCount <= 1) return null
  return (
    <div className="flex items-center justify-end gap-1 pt-1">
      {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onPageChange(p)}
          className={cn(
            'h-7 w-7 rounded-md text-xs font-medium transition-colors',
            p === page
              ? 'bg-[#4A9FD4] text-white'
              : 'text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800',
          )}
        >
          {p}
        </button>
      ))}
    </div>
  )
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

/** SerpApi key-slot credit table, paginated 4 rows at a time.
 *
 *  Serper has no card here on purpose: once its display was reduced to a
 *  single "credits spent" number, it duplicated the Serper KPI tile exactly
 *  — the same number, in a second, much larger container. A near-empty card
 *  forced into a two-column grid next to this table is exactly the kind of
 *  whitespace a real design review should remove rather than pad around.
 *
 *  The optional history line chart (design doc §6.1) is deliberately
 *  skipped: `history` is a flat {ts, slot, total_searches_left} stream
 *  across up to 13 key slots, and a pivoted single-line "pool total over
 *  time" chart didn't earn its keep over the slot table below, which already
 *  carries the operational signal (which key, how much left, whether it
 *  errored). */
function SearchApiPanel({ serpapi }: SearchApiPanelProps) {
  const slots = serpapi.data?.slots ?? []
  const totalSearchesLeft = serpapi.data?.total_searches_left ?? null

  const [page, setPage] = useState(1)
  const pageCount = Math.max(1, Math.ceil(slots.length / SERPAPI_PAGE_SIZE))
  // Clamp rather than reset to 1 on every fetch — a fresh poll reorders
  // nothing (slots are always sorted by slot number), so staying on the
  // same page across a refresh is the least surprising behaviour; this only
  // kicks in if the slot count actually shrinks below the current page.
  useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])
  const pagedSlots = slots.slice((page - 1) * SERPAPI_PAGE_SIZE, page * SERPAPI_PAGE_SIZE)

  return (
    <Card>
      <CardHeader>
        <CardTitle>SerpApi credits</CardTitle>
        <CardDescription>
          {formatInt(totalSearchesLeft)} searches left across {slots.length} key
          {slots.length === 1 ? '' : 's'}.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {serpapi.error && <InlineError message={serpapi.error} />}
        <DataTable
          columns={SERPAPI_COLUMNS}
          data={pagedSlots}
          keyExtractor={(s) => String(s.slot)}
          emptyMessage={
            serpapi.loading
              ? 'Loading…'
              : 'No credit snapshots yet — the SerpApi credit-poll beat task may not have run yet.'
          }
        />
        <Pager page={page} pageCount={pageCount} onPageChange={setPage} />
      </CardContent>
    </Card>
  )
}

export { SearchApiPanel }
