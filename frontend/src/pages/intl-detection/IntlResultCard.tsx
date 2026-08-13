import { type IntlDetectResult } from '@/hooks/useIntlDetect'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Target, Link, Tag, Percent, Layers } from 'lucide-react'
import { formatPercent } from '@/lib/utils'

function getFormatVariant(screenFormat: string): BadgeVariant {
  const f = screenFormat.toUpperCase()
  if (f === '4DX' || f === 'MX4D') return '4dx'
  if (['IMAX', 'DOLBY CINEMA', 'SCREENX', 'ONYX', 'XPLUS', 'KINOEVOLUTION', 'EPIC VUE', 'LED CINEMA'].some((k) => f.includes(k))) {
    return 'imax'
  }
  if (f === 'STANDARD') return 'standard'
  return 'circuit'
}

function getTrackVariant(track: string | null): BadgeVariant {
  if (!track) return 'default'
  if (track === 'exact') return 'success'
  if (track === 'A') return 'imax'
  if (track === 'B') return 'circuit'
  if (track === 'C') return 'warning'
  return 'default'
}

interface IntlResultCardProps {
  result: IntlDetectResult
}

function IntlResultCard({ result }: IntlResultCardProps) {
  const formatVariant = getFormatVariant(result.screen_format)
  const trackVariant = getTrackVariant(result.match_track)
  // A P5 "deliberate Standard" (match_source === "Keyword Match") must read
  // as visually distinct from a genuine no-match "Standard"
  // (match_source === "No Match") — the only field that disambiguates them.
  const isDeliberateStandard = result.screen_format.toUpperCase() === 'STANDARD' && result.match_source === 'Keyword Match'
  const isNoMatch = result.match_source === 'No Match'

  return (
    <Card className="overflow-hidden">
      <CardHeader className="bg-gradient-to-r from-zinc-50 to-zinc-100/50 dark:from-zinc-900 dark:to-zinc-800/30">
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Detection Result
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-5">
        <div className="flex flex-col gap-5">
          {/* Main format display */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-1">Detected Format</p>
              <div className="flex items-center gap-2">
                <span className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
                  {result.screen_format}
                </span>
                <Badge variant={formatVariant} size="md">
                  {formatVariant.toUpperCase()}
                </Badge>
                {isDeliberateStandard && (
                  <Badge variant="p5" size="sm">
                    P5 · Deliberate
                  </Badge>
                )}
                {isNoMatch && (
                  <Badge variant="pending" size="sm">
                    No Match
                  </Badge>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1.5 rounded-xl bg-zinc-100 dark:bg-zinc-800 px-4 py-2">
              <Percent className="h-4 w-4 text-zinc-400" />
              <span className="text-xl font-bold text-zinc-900 dark:text-zinc-50">
                {formatPercent(result.confidence)}
              </span>
            </div>
          </div>

          {/* Detail chips */}
          <div className="flex flex-wrap gap-2">
            {result.detected_keyword && (
              <div className="flex items-center gap-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-3 py-1.5 text-xs">
                <Tag className="h-3 w-3 text-zinc-400" />
                <span className="text-zinc-500 dark:text-zinc-400">Keyword:</span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">{result.detected_keyword}</span>
              </div>
            )}
            {result.match_source && (
              <div
                className={
                  isDeliberateStandard
                    ? 'flex items-center gap-1.5 rounded-lg bg-sky-50 dark:bg-sky-950/30 ring-1 ring-sky-200 dark:ring-sky-800 px-3 py-1.5 text-xs'
                    : isNoMatch
                    ? 'flex items-center gap-1.5 rounded-lg bg-orange-50 dark:bg-orange-950/30 ring-1 ring-orange-200 dark:ring-orange-800 px-3 py-1.5 text-xs'
                    : 'flex items-center gap-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-3 py-1.5 text-xs'
                }
              >
                <Link className="h-3 w-3 text-zinc-400" />
                <span className="text-zinc-500 dark:text-zinc-400">Source:</span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">{result.match_source}</span>
              </div>
            )}
            {result.match_track && (
              <div className="flex items-center gap-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-3 py-1.5 text-xs">
                <Target className="h-3 w-3 text-zinc-400" />
                <span className="text-zinc-500 dark:text-zinc-400">Track:</span>
                <Badge variant={trackVariant} size="sm">
                  {result.match_track}
                </Badge>
              </div>
            )}
            {result.priority_tier != null && (
              <div className="flex items-center gap-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-3 py-1.5 text-xs">
                <Layers className="h-3 w-3 text-zinc-400" />
                <span className="text-zinc-500 dark:text-zinc-400">Tier:</span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">P{result.priority_tier}</span>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export { IntlResultCard }
