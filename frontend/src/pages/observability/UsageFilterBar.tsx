import { useMemo, type ReactNode } from 'react'
import { AlertCircle, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Card, CardContent } from '@/components/ui/Card'
import { Select } from '@/components/ui/Select'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import type { UsageFilterState } from '@/hooks/useUsage'
import { presetRange, todayIst } from '@/hooks/useUsageFilterState'
import {
  CALLER_TYPE_OPTIONS,
  DIMENSION_LABELS,
  MARKET_OPTIONS,
  TASK_TYPE_OPTIONS,
  dimensionValueLabel,
} from './labels'

interface FilterOption {
  value: string
  label: string
}

interface UsageFilterBarProps {
  filters: UsageFilterState
  onChange: (patch: Partial<UsageFilterState>) => void
  onReset: () => void
  rangeError: string | null
  modelOptions: FilterOption[]
  apiKeyOptions: FilterOption[]
  actions?: ReactNode // Phase 5 drops the report buttons in here
}

const RANGE_PRESETS: { label: string; days: number }[] = [
  { label: 'Today', days: 1 },
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
]

/** Radix `Select`'s `shouldShowPlaceholder(value)` (@radix-ui/react-select
 *  2.3.2, dist/index.mjs) treats `value === ''` identically to `undefined` —
 *  it does not crash on an empty-string `Item`, but with `''` selected the
 *  trigger falls back to the `placeholder` prop instead of rendering the
 *  matching item's label, so "All models" etc. would never actually show.
 *  Confirmed by reading the installed package rather than assuming it.
 *  Route "all" through this sentinel only inside this component — it must
 *  never leak into UsageFilterState or buildUsageParams. */
const ALL_SENTINEL = '__all__'
function toSelectValue(v: string): string {
  return v === '' ? ALL_SENTINEL : v
}
function fromSelectValue(v: string): string {
  return v === ALL_SENTINEL ? '' : v
}
function withSentinel(options: FilterOption[]): FilterOption[] {
  return options.map((o) => (o.value === '' ? { ...o, value: ALL_SENTINEL } : o))
}

function UsageFilterBar({
  filters,
  onChange,
  onReset,
  rangeError,
  modelOptions,
  apiKeyOptions,
  actions,
}: UsageFilterBarProps) {
  const max = todayIst()

  const activePresetDays = useMemo(() => {
    const match = RANGE_PRESETS.find((p) => {
      const r = presetRange(p.days)
      return r.start === filters.start && r.end === filters.end
    })
    return match?.days ?? null
  }, [filters.start, filters.end])

  const isDefaultRange = useMemo(() => {
    const r = presetRange(7)
    return r.start === filters.start && r.end === filters.end
  }, [filters.start, filters.end])

  const hasDimensionFilters = Boolean(
    filters.taskType || filters.modelId || filters.callerType || filters.apiKeyId || filters.market,
  )
  const showReset = hasDimensionFilters || !isDefaultRange

  const CHIP_FIELD_DIMENSION_LABEL: Record<string, string> = {
    taskType: DIMENSION_LABELS.task_type,
    modelId: DIMENSION_LABELS.model_id,
    callerType: DIMENSION_LABELS.caller_type,
    apiKeyId: DIMENSION_LABELS.api_key_id,
    market: DIMENSION_LABELS.market,
  }

  const chips: { field: keyof UsageFilterState; label: string }[] = []
  if (filters.taskType) {
    chips.push({ field: 'taskType', label: dimensionValueLabel('task_type', filters.taskType) })
  }
  if (filters.modelId) {
    chips.push({
      field: 'modelId',
      label: modelOptions.find((o) => o.value === filters.modelId)?.label ?? filters.modelId,
    })
  }
  if (filters.callerType) {
    chips.push({ field: 'callerType', label: dimensionValueLabel('caller_type', filters.callerType) })
  }
  if (filters.apiKeyId) {
    chips.push({
      field: 'apiKeyId',
      label: apiKeyOptions.find((o) => o.value === filters.apiKeyId)?.label ?? filters.apiKeyId,
    })
  }
  if (filters.market) {
    chips.push({ field: 'market', label: dimensionValueLabel('market', filters.market) })
  }

  // api_key_id is only ever set for external_api calls (app/models.py:425) —
  // disable (not hide, so the layout doesn't jump) the api key filter when
  // caller is scoped to portal, and clear any stale selection.
  const apiKeyDisabled = filters.callerType === 'portal'

  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        {/* Row 1 — range presets + custom dates */}
        <div className="flex flex-wrap items-end gap-3">
          <div
            role="group"
            aria-label="Range preset"
            className="inline-flex h-9 items-center gap-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-800/60 p-1"
          >
            {RANGE_PRESETS.map((p) => (
              <button
                key={p.days}
                type="button"
                aria-pressed={activePresetDays === p.days}
                onClick={() => onChange(presetRange(p.days))}
                className={cn(
                  'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
                  'text-zinc-600 dark:text-zinc-400',
                  'hover:text-zinc-900 dark:hover:text-zinc-100',
                  activePresetDays === p.days &&
                    'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4A9FD4]/50',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Input
            type="date"
            label="Start"
            value={filters.start}
            max={max}
            onChange={(e) => onChange({ start: e.target.value })}
            className="w-40"
          />
          <Input
            type="date"
            label="End"
            value={filters.end}
            max={max}
            onChange={(e) => onChange({ end: e.target.value })}
            className="w-40"
          />
        </div>

        {/* Row 2 — dimension filters */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Select
            label={DIMENSION_LABELS.task_type}
            options={withSentinel(TASK_TYPE_OPTIONS)}
            value={toSelectValue(filters.taskType)}
            onValueChange={(v) => onChange({ taskType: fromSelectValue(v) })}
          />
          <Select
            label={DIMENSION_LABELS.model_id}
            options={withSentinel(modelOptions)}
            value={toSelectValue(filters.modelId)}
            onValueChange={(v) => onChange({ modelId: fromSelectValue(v) })}
          />
          <Select
            label={DIMENSION_LABELS.caller_type}
            options={withSentinel(CALLER_TYPE_OPTIONS)}
            value={toSelectValue(filters.callerType)}
            onValueChange={(v) => {
              const next = fromSelectValue(v)
              onChange(next === 'portal' ? { callerType: next, apiKeyId: '' } : { callerType: next })
            }}
          />
          <div className="flex flex-col gap-1">
            <Select
              label={DIMENSION_LABELS.api_key_id}
              options={withSentinel(apiKeyOptions)}
              value={toSelectValue(filters.apiKeyId)}
              onValueChange={(v) => onChange({ apiKeyId: fromSelectValue(v) })}
              disabled={apiKeyDisabled}
            />
            {apiKeyDisabled && (
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Portal calls carry no API key.
              </span>
            )}
          </div>
          <Select
            label={DIMENSION_LABELS.market}
            options={withSentinel(MARKET_OPTIONS)}
            value={toSelectValue(filters.market)}
            onValueChange={(v) => onChange({ market: fromSelectValue(v) })}
          />
        </div>

        {/* Row 3 — active-filter chips, reset, actions slot */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {chips.map((chip) => (
              <Badge key={chip.field} variant="info" className="gap-1 pr-1">
                {chip.label}
                <button
                  type="button"
                  aria-label={`Clear ${CHIP_FIELD_DIMENSION_LABEL[chip.field]} filter`}
                  onClick={() => onChange({ [chip.field]: '' } as Partial<UsageFilterState>)}
                  className="rounded-full p-0.5 hover:bg-sky-200/60 dark:hover:bg-sky-800/40"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
            {showReset && (
              <Button variant="ghost" size="sm" onClick={onReset}>
                Reset
              </Button>
            )}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>

        {rangeError && (
          <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {rangeError}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export { UsageFilterBar }
