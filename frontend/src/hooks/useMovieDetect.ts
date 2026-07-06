import { useState } from 'react'
import api from '@/lib/api'

export interface MovieDetectResult {
  movie_format: string
  detected_keyword: string | null
  match_source: string | null
  match_track: string | null
  confidence: number
  fired_ai: boolean
  ai_suggested_format: string | null
  ai_reasoning: string | null
}

interface MovieDetectPayload {
  amenity: string
}

export function useMovieDetect() {
  const [result, setResult] = useState<MovieDetectResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const detect = async (payload: MovieDetectPayload) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post<MovieDetectResult>('/api/v1/movie-detect/single', payload)
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

  return { result, loading, error, detect, reset }
}
