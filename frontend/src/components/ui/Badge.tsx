import { type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type BadgeVariant =
  | 'default'
  | 'imax'
  | '4dx'
  | 'vip'
  | 'standard'
  | 'circuit'
  | 'p1'
  | 'p2'
  | 'p3'
  | 'p4'
  | 'p5'
  | 'p6'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info'
  | 'pending'

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
  size?: 'sm' | 'md'
}

const variantMap: Record<BadgeVariant, string> = {
  default: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
  imax: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300 ring-1 ring-blue-200 dark:ring-blue-800',
  '4dx': 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300 ring-1 ring-purple-200 dark:ring-purple-800',
  vip: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-800',
  standard: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  circuit: 'bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300 ring-1 ring-teal-200 dark:ring-teal-800',
  p1: 'bg-violet-600 text-white shadow-sm',
  p2: 'bg-violet-500 text-white shadow-sm',
  p3: 'bg-indigo-500 text-white shadow-sm',
  p4: 'bg-blue-500 text-white shadow-sm',
  p5: 'bg-sky-500 text-white shadow-sm',
  p6: 'bg-zinc-400 text-white shadow-sm',
  success: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  danger: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  info: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300',
  pending: 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300',
}

const sizeMap: Record<NonNullable<BadgeProps['size']>, string> = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-2.5 py-0.5 text-xs',
}

function Badge({ variant = 'default', size = 'md', className, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md font-medium leading-tight',
        variantMap[variant],
        sizeMap[size],
        className
      )}
      {...props}
    >
      {children}
    </span>
  )
}

export { Badge, type BadgeVariant }
