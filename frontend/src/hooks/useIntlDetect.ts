import { useState } from 'react'
import api from '@/lib/api'

export interface IntlDetectResult {
  screen_format: string
  match_track: string
  confidence: number
  matched_keyword: string | null
  detected_keyword: string | null
  priority_tier: number | null
  match_source: string | null
  fired_ai: boolean
  diagnostics: Record<string, unknown> | null
}

interface IntlDetectPayload {
  amenity: string
}

export function useIntlDetect() {
  const [result, setResult] = useState<IntlDetectResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const detect = async (payload: IntlDetectPayload) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post<IntlDetectResult>('/api/v1/intl-detect/single', payload)
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
