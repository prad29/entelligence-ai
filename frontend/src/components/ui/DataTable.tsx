import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'

export interface Column<T> {
  key: keyof T | string
  header: string
  cell?: (row: T) => ReactNode
  sortable?: boolean
  className?: string
  headerClassName?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  keyExtractor: (row: T) => string
  onRowClick?: (row: T) => void
  selectable?: boolean
  selectedIds?: Set<string>
  onSelectionChange?: (ids: Set<string>) => void
  emptyMessage?: string
  className?: string
}

function DataTable<T>({
  columns,
  data,
  keyExtractor,
  onRowClick,
  selectable,
  selectedIds = new Set(),
  onSelectionChange,
  emptyMessage = 'No data found.',
  className,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = sortKey
    ? [...data].sort((a, b) => {
        const av = (a as Record<string, unknown>)[sortKey]
        const bv = (b as Record<string, unknown>)[sortKey]
        const cmp = String(av ?? '').localeCompare(String(bv ?? ''))
        return sortDir === 'asc' ? cmp : -cmp
      })
    : data

  const allSelected = data.length > 0 && data.every((row) => selectedIds.has(keyExtractor(row)))
  const someSelected = data.some((row) => selectedIds.has(keyExtractor(row)))

  const toggleAll = () => {
    if (!onSelectionChange) return
    if (allSelected) {
      onSelectionChange(new Set())
    } else {
      onSelectionChange(new Set(data.map(keyExtractor)))
    }
  }

  const toggleRow = (id: string) => {
    if (!onSelectionChange) return
    const next = new Set(selectedIds)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
    onSelectionChange(next)
  }

  return (
    <div className={cn('rounded-xl border border-zinc-200 dark:border-zinc-800 overflow-hidden', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/60">
              {selectable && (
                <th className="w-10 px-4 py-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = someSelected && !allSelected
                    }}
                    onChange={toggleAll}
                    className="rounded accent-violet-600 cursor-pointer"
                  />
                </th>
              )}
              {columns.map((col) => (
                <th
                  key={String(col.key)}
                  className={cn(
                    'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 select-none',
                    col.sortable && 'cursor-pointer hover:text-zinc-900 dark:hover:text-zinc-100',
                    col.headerClassName
                  )}
                  onClick={col.sortable ? () => handleSort(String(col.key)) : undefined}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {col.sortable && (
                      <span className="text-zinc-400">
                        {sortKey === String(col.key) ? (
                          sortDir === 'asc' ? (
                            <ChevronUp className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          )
                        ) : (
                          <ChevronsUpDown className="h-3 w-3" />
                        )}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {sorted.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (selectable ? 1 : 0)}
                  className="px-4 py-12 text-center text-zinc-400 dark:text-zinc-500"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              sorted.map((row) => {
                const id = keyExtractor(row)
                const isSelected = selectedIds.has(id)
                return (
                  <tr
                    key={id}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={cn(
                      'bg-white dark:bg-zinc-900 transition-colors duration-100',
                      'hover:bg-zinc-50 dark:hover:bg-zinc-800/60',
                      onRowClick && 'cursor-pointer',
                      isSelected && 'bg-[#4A9FD4]/10 dark:bg-[#4A9FD4]/10'
                    )}
                  >
                    {selectable && (
                      <td className="w-10 px-4 py-3">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={(e) => {
                            e.stopPropagation()
                            toggleRow(id)
                          }}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded accent-violet-600 cursor-pointer"
                        />
                      </td>
                    )}
                    {columns.map((col) => (
                      <td
                        key={String(col.key)}
                        className={cn(
                          'px-4 py-3 text-zinc-800 dark:text-zinc-200',
                          col.className
                        )}
                      >
                        {col.cell
                          ? col.cell(row)
                          : String((row as Record<string, unknown>)[String(col.key)] ?? '—')}
                      </td>
                    ))}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export { DataTable }
