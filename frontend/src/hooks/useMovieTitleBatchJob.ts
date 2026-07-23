import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export type MovieTitleBatchJobStatus = 'queued' | 'processing' | 'completed' | 'failed'
export type MovieTitleBatchMarket = 'domestic' | 'international'

export interface MovieTitleBatchJob {
  job_id: string
  status: MovieTitleBatchJobStatus
  progress: number
  total: number
  processed: number
  matched: number
  no_match: number
  failed: number
  output_url?: string
  error?: string
}

const POLL_INTERVAL_MS = 2000

function storageNamespace(market: MovieTitleBatchMarket): string {
  return market === 'international' ? 'movie-title-match-intl' : 'movie-title-match'
}

function statusUrl(market: MovieTitleBatchMarket, jobId: string): string {
  return market === 'international'
    ? `/api/v1/movie-title-match/batch/intl/${jobId}`
    : `/api/v1/movie-title-match/batch/${jobId}`
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong'
}

export function useMovieTitleBatchJob(market: MovieTitleBatchMarket = 'domestic') {
  const [job, setJob] = useState<MovieTitleBatchJob | null>(null)
  const [uploading, setUploading] = useState(false)
  const [isActive, setIsActive] = useState(false)
  const [resuming, setResuming] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const namespace = storageNamespace(market)

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
        const poll = await api.get<MovieTitleBatchJob>(statusUrl(market, jobId))
        setJob(poll.data)
        if (poll.data.status === 'completed' || poll.data.status === 'failed') {
          stopPolling()
          setIsActive(false)
          clearActiveJob(namespace)
        }
      } catch (e: unknown) {
        setError(getErrorMessage(e))
        stopPolling()
        setIsActive(false)
        clearActiveJob(namespace)
      }
    }, POLL_INTERVAL_MS)
  }

  // On mount, resume any job that was in flight when this component was
  // last unmounted (e.g. the user navigated away and back). The backend
  // job keeps running regardless of frontend state, so re-attaching to it
  // is just a status fetch away. Namespaced by market so a domestic and an
  // international job in flight at the same time never collide.
  useEffect(() => {
    const persistedJobId = loadActiveJob(namespace)
    if (!persistedJobId) {
      setResuming(false)
      return
    }

    let cancelled = false

    ;(async () => {
      try {
        const res = await api.get<MovieTitleBatchJob>(statusUrl(market, persistedJobId))
        if (cancelled) return

        setJob(res.data)
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          clearActiveJob(namespace)
        } else {
          setIsActive(true)
          startPolling(persistedJobId)
        }
      } catch {
        // Job no longer exists (TTL expired) or is otherwise unreachable —
        // drop the stale reference and fall back to the empty upload form.
        if (!cancelled) {
          clearActiveJob(namespace)
        }
      } finally {
        if (!cancelled) setResuming(false)
      }
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market])

  // Always clear any in-flight polling interval when the component unmounts,
  // even if the job never reached a terminal status.
  useEffect(() => {
    return () => {
      stopPolling()
    }
  }, [])

  const uploadBatch = async (file: File, usePosterVision: boolean) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('use_poster_vision', usePosterVision ? 'true' : 'false')
      form.append('market', market)

      const res = await api.post<{ job_id: string }>('/api/v1/movie-title-match/batch', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data
      saveActiveJob(namespace, job_id)
      startPolling(job_id)
    } catch (e: unknown) {
      setError(getErrorMessage(e))
      setIsActive(false)
    } finally {
      setUploading(false)
    }
  }

  const reset = () => {
    stopPolling()
    clearActiveJob(namespace)
    setJob(null)
    setError(null)
    setUploading(false)
    setIsActive(false)
  }

  return { job, uploading, isActive, resuming, error, uploadBatch, reset }
}
