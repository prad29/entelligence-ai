import { useState } from 'react'
import api from '@/lib/api'

export interface MovieTitleMatchPayload {
  title: string
  theater?: string
  show_date?: string
  ticketing_url?: string
  use_poster_vision?: boolean
}

export interface EliminatedCandidate {
  id: number
  title: string
  why: string
}

export interface PageMetadata {
  extracted_runtime_min?: number | null
  extracted_director?: string | null
  extracted_cast?: string | null
  extracted_rating?: string | null
  extraction_platform?: string
  extraction_tier?: string
  extraction_outcome?: string
  extracted_at?: string | null
}

export interface RuntimeCheck {
  page?: number | null
  master?: number | null
  label?: string
}

export interface DirectorCheck {
  page?: string | null
  master?: string | null
  label?: string
}

export interface AgenticSourceEvidence {
  vespa_score?: number | null
  tmdb_confirmed?: boolean
  imdb_id?: string | null
  date_proximity_days?: number | null
  ticketing_page_title?: string | null
  poster_observation?: string | null
  web_sources?: string[]
}

export interface MovieTitleMatchResult {
  suggested_movie_id: number
  suggested_movie_title: string
  canonical_movie_id: number
  confidence: number
  decision: 'AUTO_ACCEPT' | 'REVIEW' | 'REVIEW_NON_MOVIE' | 'REVIEW_MULTI_FILM'
  reasoning: string
  evidence: {
    // Mode A fields
    fuzzy_top?: Array<{ id: number; title: string; score: number }>
    date_window?: string
    eliminated?: EliminatedCandidate[]
    runtime_check?: RuntimeCheck
    director_check?: DirectorCheck
    // Mode B (agentic) fields
    agentic?: boolean
    source_evidence?: AgenticSourceEvidence
    normalized_input?: string
    event_type?: string
  }
  cover_image?: string | null
  ticketing_poster_url?: string | null
  fired_ai: boolean
  page_metadata?: PageMetadata | null
}

export function useMovieTitleMatch() {
  const [result, setResult] = useState<MovieTitleMatchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const match = async (payload: MovieTitleMatchPayload) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post<MovieTitleMatchResult>('/api/v1/movie-title-match/single', payload)
      setResult(res.data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Detection failed')
    } finally {
      setLoading(false)
    }
  }

  const reset = () => {
    setResult(null)
    setError(null)
  }

  return { result, loading, error, match, reset }
}
