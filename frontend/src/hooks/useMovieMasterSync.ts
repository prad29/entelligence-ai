import { useRef, useState } from 'react'
import api from '@/lib/api'
import type { MovieMasterMarket } from '@/hooks/useMovieMasterSeed'

export type MovieMasterSyncStatus = 'queued' | 'processing' | 'completed' | 'failed'

export interface MovieMasterSyncJob {
  job_id: string
  market: MovieMasterMarket
  status: MovieMasterSyncStatus
  total: number
  processed: number
  progress: number
  inserted: number
  updated: number
  skipped: number
  skipped_undefined_country?: number
  error?: string
}

const POLL_INTERVAL_MS = 2000

function statusUrl(market: MovieMasterMarket, jobId: string): string {
  return `/api/v1/movie-title-match/master/sync/${market}/${jobId}`
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong'
}

export function useMovieMasterSync(market: MovieMasterMarket = 'domestic') {
  const [job, setJob] = useState<MovieMasterSyncJob | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPolling = (jobId: string) => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const poll = await api.get<MovieMasterSyncJob>(statusUrl(market, jobId))
        setJob(poll.data)
        if (poll.data.status === 'completed' || poll.data.status === 'failed') {
          stopPolling()
          setSyncing(false)
        }
      } catch (e: unknown) {
        setError(getErrorMessage(e))
        stopPolling()
        setSyncing(false)
      }
    }, POLL_INTERVAL_MS)
  }

  const startSync = async () => {
    setSyncing(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const res = await api.post<{ job_id: string; status: MovieMasterSyncStatus }>(
        `/api/v1/movie-title-match/master/sync/${market}`
      )
      const { job_id } = res.data
      startPolling(job_id)
    } catch (e: unknown) {
      setError(getErrorMessage(e))
      setSyncing(false)
    }
  }

  const reset = () => {
    stopPolling()
    setJob(null)
    setError(null)
    setSyncing(false)
  }

  return { job, syncing, error, startSync, reset }
}
