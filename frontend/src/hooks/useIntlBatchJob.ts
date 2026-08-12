import { useEffect, useRef, useState } from 'react'
import api from '@/lib/api'
import { saveActiveJob, loadActiveJob, clearActiveJob } from '@/lib/persistedJob'

export interface IntlBatchJob {
  job_id: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  progress: number
  total: number
  processed: number
  matched: number
  no_match: number
  output_url?: string
  error?: string
}

// Must differ from the domestic hook's 'amenity-detect' namespace, or an
// in-flight intl job and an in-flight domestic job overwrite each other in
// localStorage via persistedJob.ts's 'batch-job:' prefix.
const STORAGE_NAMESPACE = 'intl-amenity-detect'

export function useIntlBatchJob() {
  const [job, setJob] = useState<IntlBatchJob | null>(null)
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
        const poll = await api.get<IntlBatchJob>(`/api/v1/intl-jobs/${jobId}`)
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
        const res = await api.get<IntlBatchJob>(`/api/v1/intl-jobs/${persistedJobId}`)
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

  const uploadIntlBatch = async (file: File, includeDiagnostics: boolean, auditMode?: boolean) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('include_diagnostics', String(includeDiagnostics))

      const url = auditMode ? '/api/v1/intl-detect/batch?audit_mode=true' : '/api/v1/intl-detect/batch'
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

  return { job, uploading, isActive, resuming, error, uploadIntlBatch, reset }
}
