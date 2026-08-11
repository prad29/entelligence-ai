import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export type DeletedShowtimeJobStatus = 'queued' | 'processing' | 'completed' | 'failed'
export type TheaterVerifyMode = 'off' | 'warn' | 'strict'
export type FallbackMode = 'off' | 'auto' | 'plain' | 'movie'

export interface DeletedShowtimeAdvancedOptions {
  titleMissingIsDeleted: boolean
  strictScreenCount: boolean
  theaterVerify: TheaterVerifyMode
  fallback: FallbackMode
  workers: number
}

export interface DeletedShowtimeJob {
  job_id: string
  status: DeletedShowtimeJobStatus
  progress: number
  total: number
  processed: number
  true_count: number
  false_count: number
  unknown_count: number
  original_filename?: string
  created_at?: string
  output_url?: string
  audit_url?: string
  error?: string
}

export interface DeletedShowtimePreflight {
  row_count: number
  rows_already_started: number
  now_et: string
}

const POLL_INTERVAL_MS = 2000
const NAMESPACE = 'deleted-showtimes'
const BASE_URL = '/api/v1/deleted-showtimes'

export const DEFAULT_ADVANCED_OPTIONS: DeletedShowtimeAdvancedOptions = {
  titleMissingIsDeleted: false,
  strictScreenCount: false,
  theaterVerify: 'warn',
  fallback: 'auto',
  workers: 4,
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong'
}

export function useDeletedShowtimeJob() {
  const [job, setJob] = useState<DeletedShowtimeJob | null>(null)
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
        const poll = await api.get<DeletedShowtimeJob>(`${BASE_URL}/batch/${jobId}`)
        setJob(poll.data)
        if (poll.data.status === 'completed' || poll.data.status === 'failed') {
          stopPolling()
          setIsActive(false)
          clearActiveJob(NAMESPACE)
        }
      } catch (e: unknown) {
        setError(getErrorMessage(e))
        stopPolling()
        setIsActive(false)
        clearActiveJob(NAMESPACE)
      }
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    const persistedJobId = loadActiveJob(NAMESPACE)
    if (!persistedJobId) {
      setResuming(false)
      return
    }

    let cancelled = false

    ;(async () => {
      try {
        const res = await api.get<DeletedShowtimeJob>(`${BASE_URL}/batch/${persistedJobId}`)
        if (cancelled) return

        setJob(res.data)
        if (res.data.status === 'completed' || res.data.status === 'failed') {
          clearActiveJob(NAMESPACE)
        } else {
          setIsActive(true)
          startPolling(persistedJobId)
        }
      } catch {
        if (!cancelled) {
          clearActiveJob(NAMESPACE)
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

  const preflight = async (file: File): Promise<DeletedShowtimePreflight> => {
    const form = new FormData()
    form.append('file', file)
    const res = await api.post<DeletedShowtimePreflight>(`${BASE_URL}/preflight`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return res.data
  }

  const uploadBatch = async (file: File, options: DeletedShowtimeAdvancedOptions) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('title_missing_is_deleted', options.titleMissingIsDeleted ? 'true' : 'false')
      form.append('strict_screen_count', options.strictScreenCount ? 'true' : 'false')
      form.append('theater_verify', options.theaterVerify)
      form.append('fallback', options.fallback)
      form.append('workers', String(options.workers))

      const res = await api.post<{ job_id: string }>(`${BASE_URL}/batch`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data
      saveActiveJob(NAMESPACE, job_id)
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
    clearActiveJob(NAMESPACE)
    setJob(null)
    setError(null)
    setUploading(false)
    setIsActive(false)
  }

  return { job, uploading, isActive, resuming, error, preflight, uploadBatch, reset }
}
