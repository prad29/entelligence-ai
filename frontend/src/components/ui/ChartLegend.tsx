import { cn } from '@/lib/utils'

interface ChartLegendItem {
  label: string
  color: string
  value?: string
}

interface ChartLegendProps {
  items: ChartLegendItem[]
  className?: string
}

function ChartLegend({ items, className }: ChartLegendProps) {
  return (
    <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs', className)}>
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />
          <span className="text-zinc-500 dark:text-zinc-400">{item.label}</span>
          {item.value !== undefined && (
            <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-200">{item.value}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export { ChartLegend }
export type { ChartLegendItem, ChartLegendProps }
