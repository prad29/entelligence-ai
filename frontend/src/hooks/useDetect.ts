import { useState } from 'react'
import api from '@/lib/api'

export interface DetectResult {
  amenity_string: string
  screen_format: string
  detected_keyword: string | null
  match_source: string | null
  match_track: string | null
  confidence: number
  fired_ai: boolean
  ai_reasoning: string | null
  circuit_name: string | null
}

interface DetectPayload {
  amenity_string: string
  circuit_name?: string
}

export function useDetect() {
  const [result, setResult] = useState<DetectResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const detect = async (payload: DetectPayload) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post<DetectResult>('/api/v1/detect/single', payload)
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
