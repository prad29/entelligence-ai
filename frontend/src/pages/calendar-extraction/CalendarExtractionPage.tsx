import { useRef, useState } from 'react'
import { useCalendarExtractJob } from '@/hooks/useCalendarExtractJob'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import {
  UploadCloud,
  FileText,
  X,
  Download,
  CheckCircle2,
  AlertCircle,
  Ban,
  RotateCcw,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { JobHistory } from './JobHistory'

const ACCEPTED_EXTENSIONS = '.pdf'

function CalendarExtractionPage() {
  const [file, setFile] = useState<File | null>(null)
  const [historyKey, setHistoryKey] = useState(0)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const { job, uploading, isActive, resuming, error, uploadFile, reset } = useCalendarExtractJob()

  const isCompleted = job?.status === 'completed'
  const isFailed = job?.status === 'failed'
  const isRejected = job?.status === 'rejected'
  const isRunning = job?.status === 'queued' || job?.status === 'classifying' || job?.status === 'processing'

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (!selected) return
    setFile(selected)
  }

  const handleUpload = async () => {
    if (!file) return
    await uploadFile(file)
    setHistoryKey((k) => k + 1)
  }

  const handleReset = () => {
    setFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
    reset()
    setHistoryKey((k) => k + 1)
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <UploadCloud className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Competitive Calendar Extraction</CardTitle>
              <CardDescription>
                Upload a studio theatrical release calendar PDF. It's checked first for whether it's
                actually a release calendar, then every movie row is extracted into a downloadable
                xlsx.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {resuming && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-zinc-500 dark:text-zinc-400">
              <div className="h-4 w-4 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
              Checking for an in-progress run…
            </div>
          )}

          {!job && !resuming && (
            <label
              htmlFor="calendar-extract-file"
              className={cn(
                'relative border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-150 block',
                'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50/50 dark:hover:bg-zinc-800/30'
              )}
            >
              <input
                ref={fileInputRef}
                id="calendar-extract-file"
                type="file"
                accept={ACCEPTED_EXTENSIONS}
                onChange={handleFileChange}
                className="sr-only"
              />
              {file ? (
                <div className="flex items-center justify-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-violet-100 dark:bg-violet-900/40 flex items-center justify-center">
                    <FileText className="h-5 w-5 text-violet-600 dark:text-violet-400" />
                  </div>
                  <div className="text-left">
                    <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{file.name}</p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      {(file.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      setFile(null)
                      if (fileInputRef.current) fileInputRef.current.value = ''
                    }}
                    className="ml-auto rounded-lg p-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <div className="h-12 w-12 rounded-xl bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center mb-1">
                    <UploadCloud className="h-5 w-5 text-zinc-400" />
                  </div>
                  <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                    Click to browse for a release calendar PDF
                  </p>
                  <p className="text-xs text-zinc-400 dark:text-zinc-500">
                    The same file won't be reprocessed if it's already been run
                  </p>
                </div>
              )}
            </label>
          )}

          {!job && !resuming && (
            <Button
              onClick={() => void handleUpload()}
              loading={uploading}
              disabled={!file || isActive}
              className="w-full"
            >
              <UploadCloud className="h-4 w-4" />
              Extract Calendar
            </Button>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {job && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                {isCompleted && <CheckCircle2 className="h-5 w-5 text-emerald-500" />}
                {isFailed && <AlertCircle className="h-5 w-5 text-red-500" />}
                {isRejected && <Ban className="h-5 w-5 text-amber-500" />}
                {isRunning && (
                  <div className="h-5 w-5 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
                )}
                <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 capitalize">
                  {isRunning ? 'Processing…' : job.status}
                </span>
              </div>

              {isCompleted && (
                <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 p-3 text-center">
                  <p className="text-xs text-emerald-600 dark:text-emerald-400">Rows extracted</p>
                  <p className="text-lg font-bold text-emerald-800 dark:text-emerald-300">
                    {job.rows_extracted}
                  </p>
                </div>
              )}

              {isRejected && (
                <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
                  This doesn't look like a release calendar.
                  {job.classification_reason && <p className="mt-1 opacity-90">{job.classification_reason}</p>}
                </div>
              )}

              {isCompleted && job.output_url && (
                <Button
                  variant="success"
                  onClick={() => {
                    const base = import.meta.env.VITE_API_URL ?? ''
                    window.open(`${base}${job.output_url}`, '_blank')
                  }}
                >
                  <Download className="h-4 w-4" />
                  Download xlsx
                </Button>
              )}

              {isFailed && job.error && (
                <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
                  {job.error}
                </div>
              )}

              <Button variant="ghost" size="sm" onClick={handleReset} className="self-start">
                <RotateCcw className="h-3.5 w-3.5" />
                Run Another
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <JobHistory refreshKey={historyKey} />
    </div>
  )
}

export { CalendarExtractionPage }
