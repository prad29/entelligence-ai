import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export type CalendarExtractJobStatus =
  | 'queued'
  | 'classifying'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'rejected'

export interface CalendarExtractJob {
  job_id: string
  status: CalendarExtractJobStatus
  original_filename?: string
  is_release_calendar?: boolean | null
  classification_reason?: string | null
  rows_extracted: number
  created_at?: string
  output_url?: string
  error?: string
}

const POLL_INTERVAL_MS = 2000
const NAMESPACE = 'calendar-extract'
const BASE_URL = '/api/v1/calendar-extract'
const TERMINAL_STATUSES: CalendarExtractJobStatus[] = ['completed', 'failed', 'rejected']

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong'
}

export function useCalendarExtractJob() {
  const [job, setJob] = useState<CalendarExtractJob | null>(null)
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
        const poll = await api.get<CalendarExtractJob>(`${BASE_URL}/jobs/${jobId}`)
        setJob(poll.data)
        if (TERMINAL_STATUSES.includes(poll.data.status)) {
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
        const res = await api.get<CalendarExtractJob>(`${BASE_URL}/jobs/${persistedJobId}`)
        if (cancelled) return

        setJob(res.data)
        if (TERMINAL_STATUSES.includes(res.data.status)) {
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

  const uploadFile = async (file: File) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)

      const res = await api.post<{ job_id: string; deduplicated: boolean }>(
        `${BASE_URL}/jobs`,
        form,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )

      const { job_id } = res.data
      saveActiveJob(NAMESPACE, job_id)

      const initial = await api.get<CalendarExtractJob>(`${BASE_URL}/jobs/${job_id}`)
      setJob(initial.data)
      if (TERMINAL_STATUSES.includes(initial.data.status)) {
        setIsActive(false)
        clearActiveJob(NAMESPACE)
      } else {
        startPolling(job_id)
      }
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

  return { job, uploading, isActive, resuming, error, uploadFile, reset }
}
