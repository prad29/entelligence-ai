import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { BreakdownDimension, UsageBreakdownResponse, UsageResource } from '@/hooks/useUsage'
import { CHART_GRID, CHART_SERIES, CHART_TICK } from '@/lib/chartTheme'
import { formatUsd } from '@/lib/format'
import { ChartCard } from './ChartCard'
import { ChartTooltip } from '@/components/ui/ChartTooltip'
import { topNWithOther } from './breakdownData'

interface BreakdownBarChartProps {
  title: string
  description?: string
  dimension: BreakdownDimension
  resource: UsageResource<UsageBreakdownResponse>
  height?: number
}

/** Horizontal bar chart for high-cardinality / long-label dimensions
 *  (model_id). Single-hue bars: the category axis already encodes category,
 *  so rainbow bars would add no information. The "Other" bar is
 *  de-emphasised in neutral gray. */
function BreakdownBarChart({ title, description, dimension, resource, height }: BreakdownBarChartProps) {
  const data = useMemo(
    () => topNWithOther(resource.data?.rows ?? [], dimension),
    [resource.data, dimension],
  )
  const chartHeight = height ?? Math.max(180, data.length * 34 + 40)

  return (
    <ChartCard
      title={title}
      description={description}
      height={chartHeight}
      loading={resource.loading}
      error={resource.error}
      empty={!resource.loading && !resource.error && data.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" horizontal={false} />
          <XAxis
            type="number"
            tick={CHART_TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => formatUsd(v)}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={150}
            tick={CHART_TICK}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ fill: 'rgba(113,113,122,0.08)' }}
            content={<ChartTooltip formatValue={(v) => formatUsd(v)} />}
          />
          <Bar dataKey="cost" name="Cost" radius={[0, 4, 4, 0]} maxBarSize={22}>
            {data.map((d) => (
              <Cell key={d.key} fill={d.key === '__other__' ? CHART_SERIES.neutral : CHART_SERIES.cost} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

export { BreakdownBarChart }
