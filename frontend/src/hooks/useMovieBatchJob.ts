import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export interface MovieBatchJob {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  total: number
  processed: number
  matched: number
  ai_suggestions: number
  no_match: number
  anomaly_count?: number
  output_url?: string
  error?: string
}

const STORAGE_NAMESPACE = 'movie-format-detect'

export function useMovieBatchJob() {
  const [job, setJob] = useState<MovieBatchJob | null>(null)
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
        const poll = await api.get<MovieBatchJob>(`/api/v1/movie-jobs/${jobId}`)
        setJob(poll.data)
        if (poll.data.status === 'completed' || poll.data.status === 'failed') {
          stopPolling()
          setIsActive(false)
          clearActiveJob(STORAGE_NAMESPACE)
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Polling failed')
        stopPolling()
        setIsActive(false)
        clearActiveJob(STORAGE_NAMESPACE)
      }
    }, 2000)
  }

  useEffect(() => {
    const persistedJobId = loadActiveJob(STORAGE_NAMESPACE)
    if (!persistedJobId) {
      setResuming(false)
      return
    }

    let cancelled = false

    ;(async () => {
      try {
        const res = await api.get<MovieBatchJob>(`/api/v1/movie-jobs/${persistedJobId}`)
        if (cancelled) return

        setJob(res.data)
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          clearActiveJob(STORAGE_NAMESPACE)
        } else {
          setIsActive(true)
          startPolling(persistedJobId)
        }
      } catch {
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

  useEffect(() => {
    return () => {
      stopPolling()
    }
  }, [])

  const uploadBatch = async (file: File, includeDiagnostics: boolean, batchAiMode?: string, auditMode?: boolean) => {
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

      const url = auditMode
        ? '/api/v1/movie-detect/batch?audit_mode=true'
        : '/api/v1/movie-detect/batch'

      const res = await api.post<{ job_id: string }>(url, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data
      saveActiveJob(STORAGE_NAMESPACE, job_id)
      startPolling(job_id)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
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
    setIsActive(false)
  }

  return { job, uploading, isActive, resuming, error, uploadBatch, reset }
}
