import type { ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { CHART_HEIGHT } from '@/lib/chartTheme'

interface ChartCardProps {
  title: string
  description?: string
  actions?: ReactNode // segmented controls, granularity toggle
  legend?: ReactNode
  loading?: boolean
  error?: string | null
  empty?: boolean
  emptyMessage?: string
  height?: number // px, default CHART_HEIGHT.main
  children: ReactNode
}

/** Loading / error / empty triad in one place so the four chart components
 *  stay pure render functions over already-fetched data. The height box is
 *  mandatory: Recharts' ResponsiveContainer measures its parent and renders
 *  nothing inside an auto-height flex parent. */
function ChartCard({
  title,
  description,
  actions,
  legend,
  loading,
  error,
  empty,
  emptyMessage = 'No usage recorded in this range.',
  height = CHART_HEIGHT.main,
  children,
}: ChartCardProps) {
  return (
    <Card>
      <CardHeader className="flex items-start justify-between gap-3 flex-row">
        <div className="flex flex-col gap-1.5">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </CardHeader>
      {legend && <div className="px-6 pt-3">{legend}</div>}
      <CardContent>
        <div style={{ height }} className="w-full">
          {loading ? (
            <div className="flex h-full w-full items-center justify-center">
              <div className="h-5 w-5 rounded-full border-2 border-[#4A9FD4] border-t-transparent animate-spin" />
            </div>
          ) : error ? (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-sm text-red-600 dark:text-red-400">
              <AlertCircle className="h-5 w-5" />
              <span>{error}</span>
            </div>
          ) : empty ? (
            <div className="flex h-full w-full items-center justify-center text-sm text-zinc-400 dark:text-zinc-500">
              {emptyMessage}
            </div>
          ) : (
            children
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export { ChartCard }
