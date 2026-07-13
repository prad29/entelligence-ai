import { useState } from 'react'
import { type MovieTitleMatchResult, type PageMetadata } from '@/hooks/useMovieTitleMatch'
import { Badge } from '@/components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Sparkles, Percent, ChevronDown, CheckCircle2, AlertTriangle, Eye, Globe } from 'lucide-react'
import { formatPercent } from '@/lib/utils'

function getDecisionVariant(decision: string) {
  if (decision === 'AUTO_ACCEPT') return 'imax' as const
  if (decision === 'REVIEW') return 'warning' as const
  return 'standard' as const
}

function formatRuntime(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function hasPageMetadataContent(meta: PageMetadata): boolean {
  if (!meta.extraction_outcome || meta.extraction_outcome === 'NOT_ATTEMPTED') return false
  return (
    meta.extracted_runtime_min != null ||
    meta.extracted_director != null ||
    meta.extracted_cast != null ||
    meta.extraction_platform != null ||
    meta.extraction_tier != null
  )
}

interface MovieTitleMatchCardProps {
  result: MovieTitleMatchResult
}

function MovieTitleMatchCard({ result }: MovieTitleMatchCardProps) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const decisionVariant = getDecisionVariant(result.decision)
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

        {/* Page metadata */}
        {result.page_metadata && hasPageMetadataContent(result.page_metadata) && (
          <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 px-4 py-3 flex flex-col gap-2">
            <p className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">Ticketing Page Data</p>

            {/* Extraction info chips */}
            {result.page_metadata.extraction_outcome && result.page_metadata.extraction_outcome !== 'NOT_ATTEMPTED' && (
              <div className="flex flex-wrap gap-1.5">
                {result.page_metadata.extraction_platform && (
                  <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:text-zinc-400 uppercase tracking-wide">
                    {result.page_metadata.extraction_platform}
                  </span>
                )}
                {result.page_metadata.extraction_tier && (
                  <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:text-zinc-400 uppercase tracking-wide">
                    {result.page_metadata.extraction_tier}
                  </span>
                )}
                <span className="rounded-full bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 px-2 py-0.5 text-[10px] font-medium text-zinc-600 dark:text-zinc-400 uppercase tracking-wide">
                  {result.page_metadata.extraction_outcome}
                </span>
              </div>
            )}

            {/* Runtime row */}
            {result.page_metadata.extracted_runtime_min != null && (
              <p className="text-xs text-zinc-600 dark:text-zinc-400">
                <span className="font-medium text-zinc-700 dark:text-zinc-300">Runtime:</span>{' '}
                {formatRuntime(result.page_metadata.extracted_runtime_min)} from ticketing page
              </p>
            )}

            {/* Director row */}
            {result.page_metadata.extracted_director != null && (
              <div className="flex items-center gap-1.5">
                <p className="text-xs text-zinc-600 dark:text-zinc-400">
                  <span className="font-medium text-zinc-700 dark:text-zinc-300">Director:</span>{' '}
                  {result.page_metadata.extracted_director}
                </p>
                {result.evidence?.director_check?.label?.includes('MATCH') && !result.evidence.director_check.label.includes('MISMATCH') && (
                  <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
                )}
                {result.evidence?.director_check?.label?.includes('MISMATCH') && (
                  <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                )}
              </div>
            )}

            {/* Cast row */}
            {result.page_metadata.extracted_cast != null && (
              <p className="text-xs text-zinc-600 dark:text-zinc-400 truncate">
                <span className="font-medium text-zinc-700 dark:text-zinc-300">Cast:</span>{' '}
                {result.page_metadata.extracted_cast}
              </p>
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
            <div className="flex flex-col gap-1 min-w-0">
              <p className="text-xs text-amber-700 dark:text-amber-400 leading-relaxed">
                AI reasoning was used to generate this match.
              </p>
              {/* Agentic evidence chips */}
              {result.evidence?.agentic && (
                <div className="flex flex-wrap gap-1.5 mt-0.5">
                  {result.evidence.source_evidence?.imdb_id && (
                    <span className="rounded-full bg-amber-100 dark:bg-amber-900/40 border border-amber-200 dark:border-amber-800/50 px-2 py-0.5 text-[10px] font-mono text-amber-700 dark:text-amber-400">
                      IMDb {result.evidence.source_evidence.imdb_id}
                    </span>
                  )}
                  {result.evidence.source_evidence?.date_proximity_days === 0 && (
                    <span className="rounded-full bg-green-100 dark:bg-green-900/40 border border-green-200 dark:border-green-800/50 px-2 py-0.5 text-[10px] font-medium text-green-700 dark:text-green-400">
                      exact date match
                    </span>
                  )}
                  {result.evidence.source_evidence?.tmdb_confirmed && (
                    <span className="rounded-full bg-blue-100 dark:bg-blue-900/40 border border-blue-200 dark:border-blue-800/50 px-2 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-400">
                      TMDB confirmed
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Poster observation (agentic vision) */}
        {result.evidence?.agentic && result.evidence.source_evidence?.poster_observation && (
          <div className="rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 px-4 py-3 flex gap-3">
            <Eye className="h-4 w-4 text-zinc-400 shrink-0 mt-0.5" />
            <div>
              <p className="text-xs font-semibold text-zinc-600 dark:text-zinc-300 mb-0.5">Poster analysis</p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                {result.evidence.source_evidence.poster_observation}
              </p>
            </div>
          </div>
        )}

        {/* Web sources (agentic) */}
        {result.evidence?.agentic &&
          result.evidence.source_evidence?.web_sources &&
          result.evidence.source_evidence.web_sources.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            <Globe className="h-3.5 w-3.5 text-zinc-400 mt-0.5" />
            {result.evidence.source_evidence.web_sources.map((url) => (
              <a
                key={url}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-violet-600 dark:text-violet-400 hover:underline truncate max-w-[240px]"
              >
                {url.replace(/^https?:\/\//, '')}
              </a>
            ))}
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
