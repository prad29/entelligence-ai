import * as TabsPrimitive from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'
import { type ReactNode } from 'react'

interface TabItem {
  value: string
  label: string
  count?: number
}

interface TabsProps {
  defaultValue: string
  tabs: TabItem[]
  children: ReactNode
  className?: string
}

function Tabs({ defaultValue, tabs, children, className }: TabsProps) {
  return (
    <TabsPrimitive.Root defaultValue={defaultValue} className={cn('flex flex-col gap-4', className)}>
      <TabsPrimitive.List className="inline-flex h-9 items-center gap-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-800/60 p-1">
        {tabs.map((tab) => (
          <TabsPrimitive.Trigger
            key={tab.value}
            value={tab.value}
            className={cn(
              'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
              'text-zinc-600 dark:text-zinc-400',
              'hover:text-zinc-900 dark:hover:text-zinc-100',
              'data-[state=active]:bg-white dark:data-[state=active]:bg-zinc-900',
              'data-[state=active]:text-zinc-900 dark:data-[state=active]:text-zinc-50',
              'data-[state=active]:shadow-sm',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50'
            )}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="rounded-full bg-zinc-200 dark:bg-zinc-700 px-1.5 py-0.5 text-[10px] font-semibold leading-none tabular-nums">
                {tab.count}
              </span>
            )}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  )
}

function TabsContent({ value, children, className }: { value: string; children: ReactNode; className?: string }) {
  return (
    <TabsPrimitive.Content
      value={value}
      className={cn('focus-visible:outline-none', className)}
    >
      {children}
    </TabsPrimitive.Content>
  )
}

export { Tabs, TabsContent }
