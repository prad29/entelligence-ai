import { useState, useRef } from 'react'
import api from '@/lib/api'

export interface MovieBatchJob {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  total: number
  processed: number
  matched: number
  ai_suggestions: number
  output_url?: string
  error?: string
}

export function useMovieBatchJob() {
  const [job, setJob] = useState<MovieBatchJob | null>(null)
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

  const uploadBatch = async (file: File, includeDiagnostics: boolean, batchAiMode?: string) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('include_diagnostics', String(includeDiagnostics))
      form.append('batch_ai_mode', batchAiMode ?? 'skip')

      const res = await api.post<{ job_id: string }>('/api/v1/movie-detect/batch', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data

      pollRef.current = setInterval(async () => {
        try {
          const poll = await api.get<MovieBatchJob>(`/api/v1/movie-jobs/${job_id}`)
          setJob(poll.data)
          if (poll.data.status === 'completed' || poll.data.status === 'failed') {
            stopPolling()
            setIsActive(false)
          }
        } catch (e: unknown) {
          setError(e instanceof Error ? e.message : 'Polling failed')
          stopPolling()
          setIsActive(false)
        }
      }, 2000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
      setIsActive(false)
    } finally {
      setUploading(false)
    }
  }

  const reset = () => {
    stopPolling()
    setJob(null)
    setError(null)
    setIsActive(false)
  }

  return { job, uploading, isActive, error, uploadBatch, reset }
}
