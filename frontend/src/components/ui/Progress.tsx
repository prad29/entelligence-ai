import * as ProgressPrimitive from '@radix-ui/react-progress'
import { cn } from '@/lib/utils'

interface ProgressProps {
  value: number
  className?: string
  indicatorClassName?: string
}

function Progress({ value, className, indicatorClassName }: ProgressProps) {
  return (
    <ProgressPrimitive.Root
      className={cn(
        'relative h-2 w-full overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800',
        className
      )}
      value={value}
    >
      <ProgressPrimitive.Indicator
        className={cn(
          'h-full rounded-full transition-all duration-500 ease-out bg-violet-600',
          indicatorClassName
        )}
        style={{ transform: `translateX(-${100 - (value || 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
