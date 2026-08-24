import { useMemo } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { BreakdownDimension, UsageBreakdownResponse, UsageResource } from '@/hooks/useUsage'
import { CHART_HEIGHT, CHART_SERIES, categoricalColor } from '@/lib/chartTheme'
import { formatPct, formatUsd } from '@/lib/format'
import { ChartCard } from './ChartCard'
import { ChartTooltip } from '@/components/ui/ChartTooltip'
import { ChartLegend } from '@/components/ui/ChartLegend'
import { sumCost, topNWithOther } from './breakdownData'

interface BreakdownDonutChartProps {
  title: string
  description?: string
  dimension: BreakdownDimension
  resource: UsageResource<UsageBreakdownResponse>
}

/** Donut for low-cardinality part-of-whole dimensions (task_type,
 *  caller_type, both <=5 slices). No slice labels or leader lines — the
 *  ChartLegend passed into ChartCard's legend slot *is* the label layer, so
 *  it stays readable at any slice size, including thin "Other" wedges. */
function BreakdownDonutChart({ title, description, dimension, resource }: BreakdownDonutChartProps) {
  const data = useMemo(
    () => topNWithOther(resource.data?.rows ?? [], dimension),
    [resource.data, dimension],
  )
  const total = useMemo(() => sumCost(data), [data])

  // Guard total === 0 before dividing: a zero-cost range with nonzero
  // requests is real (e.g. an all-cache-hit window), and it should render
  // the empty state rather than NaN percentages.
  const isEmpty = !resource.loading && !resource.error && (data.length === 0 || total === 0)

  const legendItems = data.map((d, i) => ({
    label: d.label,
    color: d.key === '__other__' ? CHART_SERIES.neutral : categoricalColor(i),
    value: total > 0 ? `${formatUsd(d.cost)} · ${formatPct(d.cost / total)}` : formatUsd(d.cost),
  }))

  return (
    <ChartCard
      title={title}
      description={description}
      height={CHART_HEIGHT.panel}
      legend={!isEmpty && <ChartLegend items={legendItems} />}
      loading={resource.loading}
      error={resource.error}
      empty={isEmpty}
      emptyMessage="No cost recorded in this range."
    >
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="cost"
            nameKey="label"
            innerRadius={58}
            outerRadius={84}
            paddingAngle={2}
            stroke="none"
            isAnimationActive={false}
          >
            {data.map((d, i) => (
              <Cell key={d.key} fill={d.key === '__other__' ? CHART_SERIES.neutral : categoricalColor(i)} />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip formatValue={(v) => formatUsd(v)} />} />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

export { BreakdownDonutChart }
