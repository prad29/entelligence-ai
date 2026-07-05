import { type MovieDetectResult } from '@/hooks/useMovieDetect'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Sparkles, Target, Link, Tag, Percent } from 'lucide-react'
import { formatPercent } from '@/lib/utils'

function getFormatVariant(movieFormat: string): BadgeVariant {
  const f = movieFormat.toUpperCase()
  if (f === '70MM') return 'p1'
  if (f === '35MM') return 'p2'
  if (f === '3D') return 'imax'
  return 'standard'
}

function getTrackVariant(track: string | null): BadgeVariant {
  if (!track) return 'default'
  if (track === 'A') return 'imax'
  if (track === 'B') return 'circuit'
  if (track === 'C') return 'warning'
  return 'default'
}

interface MovieResultCardProps {
  result: MovieDetectResult
}

function MovieResultCard({ result }: MovieResultCardProps) {
  const formatVariant = getFormatVariant(result.movie_format)
  const trackVariant = getTrackVariant(result.match_track)

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
                  {result.movie_format}
                </span>
                <Badge variant={formatVariant} size="md">
                  {result.movie_format}
                </Badge>
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
              <div className="flex items-center gap-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-3 py-1.5 text-xs">
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
          </div>

          {/* AI callout */}
          {result.fired_ai && (
            <div className="rounded-xl border border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 flex gap-3">
              <Sparkles className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-amber-800 dark:text-amber-300 mb-1">
                  AI Suggestion
                  {result.ai_suggested_format && (
                    <span className="ml-2 inline-flex items-center rounded-md bg-amber-100 dark:bg-amber-900/50 px-2 py-0.5 font-bold text-amber-900 dark:text-amber-200 ring-1 ring-amber-300 dark:ring-amber-700">
                      {result.ai_suggested_format}
                    </span>
                  )}
                </p>
                {result.ai_reasoning ? (
                  <p className="text-xs text-amber-700 dark:text-amber-400 leading-relaxed">{result.ai_reasoning}</p>
                ) : (
                  <p className="text-xs text-amber-600 dark:text-amber-500 italic">
                    No keyword match — added to Review Queue for human approval.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export { MovieResultCard }
