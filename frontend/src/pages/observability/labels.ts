import type { BreakdownDimension } from '@/hooks/useUsage'

/** Mirrors app/observability/constants.py TASK_TYPES — all four, including
 *  movie_format_detection which design §5 omits but the code emits. */
export const TASK_TYPE_LABELS: Record<string, string> = {
  domestic_mapping: 'Domestic mapping',
  intl_mapping: 'International mapping',
  amenity_detection: 'Amenity detection',
  movie_format_detection: 'Movie format detection',
}

/** CALLER_TYPES — portal is one unattributed bucket (no portal auth, §3). */
export const CALLER_TYPE_LABELS: Record<string, string> = {
  portal: 'Portal',
  external_api: 'External API',
}

export const MARKET_LABELS: Record<string, string> = {
  domestic: 'Domestic',
  international: 'International',
}

export const DIMENSION_LABELS: Record<BreakdownDimension, string> = {
  task_type: 'Task type',
  model_id: 'Model',
  caller_type: 'Caller',
  api_key_id: 'API key',
  market: 'Market',
}

/** 'us.anthropic.claude-sonnet-5-20250929-v1:0' -> 'claude-sonnet-5' */
export function shortenModelId(modelId: string): string {
  return modelId
    .replace(/^(us|eu|apac)\./, '')
    .replace(/^anthropic\./, '')
    .replace(/-\d{8}(-v\d+:\d+)?$/, '')
    .replace(/-v\d+:\d+$/, '')
}

/** '' is a real key: the rollup stores '' where the raw log stores NULL. */
export function dimensionValueLabel(dimension: BreakdownDimension, value: string): string {
  if (!value) return '(none)'
  if (dimension === 'task_type') return TASK_TYPE_LABELS[value] ?? value
  if (dimension === 'caller_type') return CALLER_TYPE_LABELS[value] ?? value
  if (dimension === 'market') return MARKET_LABELS[value] ?? value
  if (dimension === 'model_id') return shortenModelId(value)
  return value
}

export const TASK_TYPE_OPTIONS = [
  { value: '', label: 'All task types' },
  ...Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({ value, label })),
]
export const CALLER_TYPE_OPTIONS = [
  { value: '', label: 'All callers' },
  ...Object.entries(CALLER_TYPE_LABELS).map(([value, label]) => ({ value, label })),
]
export const MARKET_OPTIONS = [
  { value: '', label: 'All markets' },
  ...Object.entries(MARKET_LABELS).map(([value, label]) => ({ value, label })),
]
