import { cn } from '@/lib/utils'

interface TooltipEntry {
  name?: string
  dataKey?: string | number
  value?: number | string
  color?: string
}

interface ChartTooltipProps {
  active?: boolean
  payload?: TooltipEntry[]
  label?: string | number
  /** Renders each entry's value; receives the raw numeric value + dataKey. */
  formatValue?: (value: number, dataKey: string) => string
  /** Overrides the header line. */
  formatLabel?: (label: string) => string
  className?: string
}

function ChartTooltip({ active, payload, label, formatValue, formatLabel, className }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className={cn(
      'rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900',
      'px-3 py-2 shadow-lg text-xs min-w-[9rem]',
      className,
    )}>
      {label !== undefined && (
        <p className="mb-1.5 font-semibold text-zinc-900 dark:text-zinc-50">
          {formatLabel ? formatLabel(String(label)) : String(label)}
        </p>
      )}
      <div className="flex flex-col gap-1">
        {payload.map((entry, i) => (
          <div key={`${String(entry.dataKey)}-${i}`} className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: entry.color }} />
            <span className="text-zinc-500 dark:text-zinc-400">{entry.name ?? String(entry.dataKey)}</span>
            <span className="ml-auto font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
              {typeof entry.value === 'number' && formatValue
                ? formatValue(entry.value, String(entry.dataKey))
                : String(entry.value ?? '—')}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export { ChartTooltip }
