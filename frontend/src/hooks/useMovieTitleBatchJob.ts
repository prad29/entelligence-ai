import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'

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
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

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

      pollRef.current = setInterval(async () => {
        try {
          const poll = await api.get<MovieTitleBatchJob>(`/api/v1/movie-title-match/batch/${job_id}`)
          setJob(poll.data)
          if (poll.data.status === 'completed' || poll.data.status === 'failed') {
            stopPolling()
            setIsActive(false)
          }
        } catch (e: unknown) {
          setError(getErrorMessage(e))
          stopPolling()
          setIsActive(false)
        }
      }, POLL_INTERVAL_MS)
    } catch (e: unknown) {
      setError(getErrorMessage(e))
      setIsActive(false)
    } finally {
      setUploading(false)
    }
  }

  const reset = () => {
    stopPolling()
    setJob(null)
    setError(null)
    setUploading(false)
    setIsActive(false)
  }

  return { job, uploading, isActive, error, uploadBatch, reset }
}
