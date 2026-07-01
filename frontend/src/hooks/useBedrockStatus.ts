import { useState, useEffect } from 'react'
import api from '@/lib/api'

interface BedrockStatus {
  connected: boolean
  model_id?: string
  region?: string
}

export function useBedrockStatus(intervalMs = 30_000) {
  const [status, setStatus] = useState<BedrockStatus | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const fetchStatus = async () => {
      try {
        const res = await api.get<BedrockStatus>('/api/v1/settings/bedrock/status')
        if (!cancelled) setStatus(res.data)
      } catch {
        if (!cancelled) setStatus({ connected: false })
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void fetchStatus()
    const timer = setInterval(() => { void fetchStatus() }, intervalMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [intervalMs])

  return { status, loading }
}
