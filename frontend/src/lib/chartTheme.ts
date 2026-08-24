/** Series colors for named metrics. Mid-luminance on purpose: one value per
 *  series reads correctly on both `bg-white` and `bg-zinc-900`, so no
 *  theme-aware swapping is needed anywhere. */
export const CHART_SERIES = {
  cost: '#3F8FCB',          // brand-adjacent blue (#4A9FD4 darkened for contrast on white)
  requests: '#7A6BD1',      // violet
  inputTokens: '#3F8FCB',
  outputTokens: '#2FA189',  // teal
  neutral: '#8A8A94',       // "Other" / de-emphasised marks
} as const

/** Categorical ramp for slice-encoded charts only (donuts). Ordered by
 *  perceptual distance, max 6 — anything beyond that becomes "Other". */
export const CHART_CATEGORICAL = [
  '#3F8FCB', '#7A6BD1', '#2FA189', '#D9903A', '#C96A87', '#5E7E9B',
] as const

export const CHART_GRID = 'rgba(113,113,122,0.25)'   // zinc-500 @ 25%
export const CHART_AXIS_LINE = 'rgba(113,113,122,0.35)'
export const CHART_TICK = { fill: '#8A8A94', fontSize: 11 } as const

/** Chart body heights in px, matching Tailwind h-72 / h-60. */
export const CHART_HEIGHT = { main: 288, panel: 240 } as const

export function categoricalColor(index: number): string {
  return CHART_CATEGORICAL[index % CHART_CATEGORICAL.length]
}
