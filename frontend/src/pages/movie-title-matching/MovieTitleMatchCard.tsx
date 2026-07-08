import { useState } from 'react'
import { type MovieTitleMatchResult } from '@/hooks/useMovieTitleMatch'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Sparkles, Percent, ChevronDown } from 'lucide-react'
import { formatPercent } from '@/lib/utils'

function getDecisionVariant(decision: string) {
  if (decision === 'AUTO_ACCEPT') return 'imax' as const
  if (decision === 'REVIEW') return 'warning' as const
  return 'standard' as const
}

interface MovieTitleMatchCardProps {
  result: MovieTitleMatchResult
}

function MovieTitleMatchCard({ result }: MovieTitleMatchCardProps) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const decisionVariant = getDecisionVariant(result.decision)
  const isNotSeeded = result.suggested_movie_id === 0

  if (isNotSeeded) return null

  return (
    <Card className="overflow-hidden">
      <CardHeader className="bg-gradient-to-r from-zinc-50 to-zinc-100/50 dark:from-zinc-900 dark:to-zinc-800/30">
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Match Result
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-5 flex flex-col gap-5">

        {/* Top row: title + confidence + decision */}
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <p className="text-xs text-zinc-500 mb-1">Matched Title</p>
            <p className="text-2xl font-bold text-zinc-900 dark:text-zinc-50">
              {result.suggested_movie_title}
            </p>
            <p className="text-xs text-zinc-400 mt-0.5">ID: {result.suggested_movie_id}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 rounded-xl bg-zinc-100 dark:bg-zinc-800 px-4 py-2">
              <Percent className="h-4 w-4 text-zinc-400" />
              <span className="text-xl font-bold text-zinc-900 dark:text-zinc-50">
                {formatPercent(result.confidence)}
              </span>
            </div>
            <Badge variant={decisionVariant} size="md">
              {result.decision.replaceAll('_', ' ')}
            </Badge>
          </div>
        </div>

        {/* Posters side-by-side (if available) */}
        {(result.cover_image || result.ticketing_poster_url) && (
          <div className="flex gap-3">
            {result.cover_image && (
              <div className="flex flex-col gap-1 items-center">
                <p className="text-[10px] text-zinc-400 uppercase tracking-wide">Movie Master Poster</p>
                <img
                  src={result.cover_image}
                  alt={result.suggested_movie_title}
                  loading="lazy"
                  className="h-32 w-auto rounded-lg object-cover border border-zinc-200 dark:border-zinc-700"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              </div>
            )}
            {result.ticketing_poster_url && (
              <div className="flex flex-col gap-1 items-center">
                <p className="text-[10px] text-zinc-400 uppercase tracking-wide">Ticketing Page Poster</p>
                <img
                  src={result.ticketing_poster_url}
                  alt="Ticketing poster"
                  loading="lazy"
                  className="h-32 w-auto rounded-lg object-cover border border-zinc-200 dark:border-zinc-700"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              </div>
            )}
          </div>
        )}

        {/* Reasoning */}
        {result.reasoning && (
          <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 px-4 py-3">
            <p className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">Reasoning</p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">{result.reasoning}</p>
          </div>
        )}

        {/* AI callout */}
        {result.fired_ai && (
          <div className="rounded-xl border border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 flex gap-3">
            <Sparkles className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-700 dark:text-amber-400 leading-relaxed">
              AI reasoning was used to generate this match.
            </p>
          </div>
        )}

        {/* Evidence accordion */}
        <button
          onClick={() => setEvidenceOpen(!evidenceOpen)}
          className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
        >
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${evidenceOpen ? 'rotate-180' : ''}`} />
          {evidenceOpen ? 'Hide' : 'Show'} evidence
        </button>
        {evidenceOpen && (
          <pre className="text-xs text-zinc-500 dark:text-zinc-400 bg-zinc-50 dark:bg-zinc-900 rounded-lg p-3 overflow-x-auto border border-zinc-200 dark:border-zinc-800">
            {JSON.stringify(result.evidence, null, 2)}
          </pre>
        )}

        {/* Eliminated candidates */}
        {result.evidence?.eliminated && result.evidence.eliminated.length > 0 && (
          <div>
            <p className="text-xs font-medium text-zinc-500 mb-1.5">Eliminated candidates:</p>
            <div className="flex flex-wrap gap-1.5">
              {result.evidence.eliminated.map((e) => (
                <span
                  key={e.id}
                  className="rounded-full border border-red-200 dark:border-red-800/50 bg-red-50 dark:bg-red-950/20 px-2.5 py-0.5 text-xs text-red-700 dark:text-red-400"
                  title={e.why}
                >
                  {e.title}
                </span>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export { MovieTitleMatchCard }
