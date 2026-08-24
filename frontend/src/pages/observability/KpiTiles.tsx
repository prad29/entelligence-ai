import type { ReactNode } from 'react'
import { Card } from '@/components/ui/Card'
import { cn } from '@/lib/utils'
import { formatCompact, formatInt, formatPct, formatUsd } from '@/lib/format'
import type {
  SerpApiCreditsResponse,
  SerperUsageResponse,
  UsageDedupeResponse,
  UsageResource,
  UsageSummaryResponse,
} from '@/hooks/useUsage'

interface KpiTileProps {
  label: string
  value: string
  sub?: string
  icon?: ReactNode
  tone?: 'default' | 'warning' | 'danger'
  loading?: boolean
  title?: string // native tooltip for the caveats (e.g. Serper drift)
}

const toneClasses: Record<NonNullable<KpiTileProps['tone']>, string> = {
  default: 'text-zinc-900 dark:text-zinc-50',
  warning: 'text-amber-600 dark:text-amber-400',
  danger: 'text-red-600 dark:text-red-400',
}

function KpiTile({ label, value, sub, icon, tone = 'default', loading, title }: KpiTileProps) {
  return (
    <Card className="px-4 py-3.5 flex flex-col gap-1" title={title}>
      <span className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
        {icon}
        {label}
      </span>
      {loading ? (
        <div className="h-6 w-20 rounded bg-zinc-100 dark:bg-zinc-800 animate-pulse" />
      ) : (
        <span className={cn('text-xl font-semibold tabular-nums', toneClasses[tone])}>{value}</span>
      )}
      {sub && <span className="text-[11px] text-zinc-500 dark:text-zinc-400">{sub}</span>}
    </Card>
  )
}

interface KpiTilesProps {
  summary: UsageResource<UsageSummaryResponse>
  dedupe: UsageResource<UsageDedupeResponse>
  serpapi: UsageResource<SerpApiCreditsResponse>
  serper: UsageResource<SerperUsageResponse>
}

function KpiTiles({ summary, dedupe, serpapi, serper }: KpiTilesProps) {
  const totals = summary.data?.totals
  const derived = summary.data?.derived
  const failureRate = derived?.failure_rate ?? null

  const slots = serpapi.data?.slots ?? []
  const totalSearchesLeft = serpapi.data?.total_searches_left ?? null
  const erroredSlots = slots.filter((s) => s.error).length
  const serpapiSub = serpapi.data
    ? `${slots.length} keys${erroredSlots > 0 ? `, ${erroredSlots} errored` : ''}`
    : undefined
  const serpapiTone: KpiTileProps['tone'] =
    totalSearchesLeft != null && totalSearchesLeft < 100
      ? 'danger'
      : totalSearchesLeft != null && totalSearchesLeft < 1000
        ? 'warning'
        : 'default'

  const quotaConfigured = serper.data?.quota_configured ?? false
  const serperValue = serper.data
    ? quotaConfigured
      ? formatInt(serper.data.remaining)
      : formatInt(serper.data.used)
    : formatInt(null)
  const serperSub = serper.data
    ? quotaConfigured
      ? `of ${formatInt(serper.data.quota_total)} remaining`
      : 'used — quota not configured'
    : undefined

  const overall = dedupe.data?.overall

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      <KpiTile
        label="Total cost"
        value={formatUsd(totals?.cost_usd)}
        sub={`${formatUsd(derived?.cost_per_request)} / request`}
        loading={summary.loading}
        title={summary.error ?? undefined}
      />
      <KpiTile
        label="Requests"
        value={formatInt(totals?.request_count)}
        sub={`${formatPct(failureRate)} failed`}
        tone={failureRate != null && failureRate > 0.05 ? 'danger' : 'default'}
        loading={summary.loading}
        title={summary.error ?? undefined}
      />
      <KpiTile
        label="Tokens"
        value={formatCompact(
          totals ? totals.input_tokens + totals.output_tokens : null,
        )}
        sub={`${formatCompact(totals?.input_tokens)} in / ${formatCompact(totals?.output_tokens)} out`}
        loading={summary.loading}
        title={summary.error ?? undefined}
      />
      <KpiTile
        label="SerpApi credits"
        value={formatInt(totalSearchesLeft)}
        sub={serpapiSub}
        tone={serpapiTone}
        loading={serpapi.loading}
        title={serpapi.error ?? undefined}
      />
      <KpiTile
        label="Serper usage"
        value={serperValue}
        sub={serperSub}
        tone={!quotaConfigured ? 'warning' : 'default'}
        loading={serper.loading}
        title={serper.error ?? serper.data?.warning ?? undefined}
      />
      <KpiTile
        label="Dedupe rate"
        value={formatPct(overall?.dedupe_rate)}
        sub={`saved ~${formatUsd(overall?.estimated_savings_usd)}`}
        loading={dedupe.loading}
        title={dedupe.error ?? undefined}
      />
    </div>
  )
}

export { KpiTile, KpiTiles }
