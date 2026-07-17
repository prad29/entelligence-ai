import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export type MovieTitleBatchJobStatus = 'queued' | 'processing' | 'completed' | 'failed'

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
const STORAGE_NAMESPACE = 'movie-title-match'

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong'
}

export function useMovieTitleBatchJob() {
  const [job, setJob] = useState<MovieTitleBatchJob | null>(null)
  const [uploading, setUploading] = useState(false)
  const [isActive, setIsActive] = useState(false)
  const [resuming, setResuming] = useState(true)
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
        const poll = await api.get<MovieTitleBatchJob>(`/api/v1/movie-title-match/batch/${jobId}`)
        setJob(poll.data)
        if (poll.data.status === 'completed' || poll.data.status === 'failed') {
          stopPolling()
          setIsActive(false)
          clearActiveJob(STORAGE_NAMESPACE)
        }
      } catch (e: unknown) {
        setError(getErrorMessage(e))
        stopPolling()
        setIsActive(false)
        clearActiveJob(STORAGE_NAMESPACE)
      }
    }, POLL_INTERVAL_MS)
  }

  // On mount, resume any job that was in flight when this component was
  // last unmounted (e.g. the user navigated away and back). The backend
  // job keeps running regardless of frontend state, so re-attaching to it
  // is just a status fetch away.
  useEffect(() => {
    const persistedJobId = loadActiveJob(STORAGE_NAMESPACE)
    if (!persistedJobId) {
      setResuming(false)
      return
    }

    let cancelled = false

    ;(async () => {
      try {
        const res = await api.get<MovieTitleBatchJob>(`/api/v1/movie-title-match/batch/${persistedJobId}`)
        if (cancelled) return

        setJob(res.data)
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          clearActiveJob(STORAGE_NAMESPACE)
        } else {
          setIsActive(true)
          startPolling(persistedJobId)
        }
      } catch {
        // Job no longer exists (TTL expired) or is otherwise unreachable —
        // drop the stale reference and fall back to the empty upload form.
        if (!cancelled) {
          clearActiveJob(STORAGE_NAMESPACE)
        }
      } finally {
        if (!cancelled) setResuming(false)
      }
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

      const res = await api.post<{ job_id: string }>('/api/v1/movie-title-match/batch', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data
      saveActiveJob(STORAGE_NAMESPACE, job_id)
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
    clearActiveJob(STORAGE_NAMESPACE)
    setJob(null)
    setError(null)
    setUploading(false)
    setIsActive(false)
  }

  return { job, uploading, isActive, resuming, error, uploadBatch, reset }
}
