import { useState, useRef } from 'react'
import api from '@/lib/api'

export interface BatchJob {
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

export function useBatchJob() {
  const [job, setJob] = useState<BatchJob | null>(null)
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

  const uploadBatch = async (file: File, includeDiagnostics: boolean, auditMode?: boolean) => {
    setUploading(true)
    setIsActive(true)
    setError(null)
    setJob(null)
    stopPolling()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('include_diagnostics', String(includeDiagnostics))

      const url = auditMode ? '/api/v1/detect/batch?audit_mode=true' : '/api/v1/detect/batch'
      const res = await api.post<{ job_id: string }>(url, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })

      const { job_id } = res.data

      pollRef.current = setInterval(async () => {
        try {
          const poll = await api.get<BatchJob>(`/api/v1/jobs/${job_id}`)
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
