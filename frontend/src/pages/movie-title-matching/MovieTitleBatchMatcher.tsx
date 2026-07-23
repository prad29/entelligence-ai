import { useRef, useState } from 'react'
import { useMovieTitleBatchJob } from '@/hooks/useMovieTitleBatchJob'
import { Button } from '@/components/ui/Button'
import { Progress } from '@/components/ui/Progress'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { UploadCloud, FileSpreadsheet, Eye, X, Download, CheckCircle2, AlertCircle, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Market } from './MovieTitleMatchingPage'

const ACCEPTED_EXTENSIONS = '.csv,.xlsx'

interface MovieTitleBatchMatcherProps {
  market: Market
}

function MovieTitleBatchMatcher({ market }: MovieTitleBatchMatcherProps) {
  const [file, setFile] = useState<File | null>(null)
  const [posterVision, setPosterVision] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const { job, uploading, isActive, resuming, error, uploadBatch, reset } = useMovieTitleBatchJob(market)
  const isIntl = market === 'international'

  const isCompleted = job?.status === 'completed'
  const isFailed = job?.status === 'failed'
  const isRunning = job?.status === 'queued' || job?.status === 'processing'
  const progressPct = job && job.total > 0 ? Math.round((job.processed / job.total) * 100) : 0

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (selected) setFile(selected)
  }

  const handleUpload = async () => {
    if (!file) return
    await uploadBatch(file, posterVision)
  }

  const handleReset = () => {
    setFile(null)
    setPosterVision(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
    reset()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <UploadCloud className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Batch Upload {isIntl && '(International)'}</CardTitle>
              <CardDescription>
                Upload a CSV or XLSX with{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">movie_title</code>,{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">show_date</code>,{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">ticketing_url</code>
                {isIntl && (
                  <>
                    , and{' '}
                    <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">country</code>
                  </>
                )}{' '}
                columns to match titles in bulk.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {/* Resuming a persisted job after remount */}
          {resuming && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-zinc-500 dark:text-zinc-400">
              <div className="h-4 w-4 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
              Checking for an in-progress batch…
            </div>
          )}

          {/* File input */}
          {!job && !resuming && (
            <div className="flex flex-col gap-2">
              <label
                htmlFor="batch-file"
                className={cn(
                  'relative border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-150 block',
                  'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50/50 dark:hover:bg-zinc-800/30'
                )}
              >
                <input
                  ref={fileInputRef}
                  id="batch-file"
                  type="file"
                  accept={ACCEPTED_EXTENSIONS}
                  onChange={handleFileChange}
                  className="sr-only"
                />
                {file ? (
                  <div className="flex items-center justify-center gap-3">
                    <div className="h-10 w-10 rounded-lg bg-violet-100 dark:bg-violet-900/40 flex items-center justify-center">
                      <FileSpreadsheet className="h-5 w-5 text-violet-600 dark:text-violet-400" />
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
                      Click to browse for a CSV or XLSX file
                    </p>
                    <p className="text-xs text-zinc-400 dark:text-zinc-500">
                      Requires <code className="font-mono text-[11px]">movie_title</code>,{' '}
                      <code className="font-mono text-[11px]">show_date</code>,{' '}
                      <code className="font-mono text-[11px]">ticketing_url</code>
                      {isIntl && (
                        <>, <code className="font-mono text-[11px]">country</code></>
                      )}
                    </p>
                  </div>
                )}
              </label>
            </div>
          )}

          {/* Poster vision toggle (visually identical to Single Match) */}
          {!job && !resuming && (
            <button
              type="button"
              onClick={() => setPosterVision(!posterVision)}
              className={cn(
                'flex items-center gap-3 rounded-xl border px-4 py-3 text-sm transition-all duration-200 w-full text-left',
                posterVision
                  ? 'border-violet-400 dark:border-violet-600 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                  : 'border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/40'
              )}
            >
              {/* Track */}
              <div className={cn(
                'relative h-5 w-9 rounded-full transition-colors duration-200 shrink-0',
                posterVision ? 'bg-violet-500' : 'bg-zinc-300 dark:bg-zinc-600'
              )}>
                {/* Thumb */}
                <span className={cn(
                  'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
                  posterVision ? 'translate-x-4' : 'translate-x-0'
                )} />
              </div>
              <Eye className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium">Poster Vision</span>
              <span className="ml-auto text-xs opacity-60 shrink-0">
                {posterVision ? 'AI inspects DB posters · slower' : 'Faster · no image analysis'}
              </span>
            </button>
          )}

          {/* Upload button */}
          {!job && !resuming && (
            <Button
              onClick={() => void handleUpload()}
              loading={uploading}
              disabled={!file || isActive}
              className="w-full"
            >
              <UploadCloud className="h-4 w-4" />
              Start Batch Match
            </Button>
          )}

          {/* Error (upload-time) */}
          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {/* Job progress */}
          {job && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {isCompleted && <CheckCircle2 className="h-5 w-5 text-emerald-500" />}
                  {isFailed && <AlertCircle className="h-5 w-5 text-red-500" />}
                  {isRunning && (
                    <div className="h-5 w-5 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
                  )}
                  <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 capitalize">
                    {isRunning ? 'Processing…' : job.status}
                  </span>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <Progress
                  value={progressPct}
                  indicatorClassName={isCompleted ? 'bg-emerald-500' : isFailed ? 'bg-red-500' : 'bg-violet-600'}
                />
                <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
                  <span>{progressPct}%</span>
                  <span>{job.processed} / {job.total} rows</span>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 p-3 text-center">
                  <p className="text-xs text-emerald-600 dark:text-emerald-400">Matched</p>
                  <p className="text-lg font-bold text-emerald-800 dark:text-emerald-300">{job.matched}</p>
                </div>
                <div className="rounded-lg bg-zinc-50 dark:bg-zinc-800 p-3 text-center">
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">No Match</p>
                  <p className="text-lg font-bold text-zinc-900 dark:text-zinc-50">{job.no_match}</p>
                </div>
                <div className="rounded-lg bg-red-50 dark:bg-red-950/30 p-3 text-center">
                  <p className="text-xs text-red-600 dark:text-red-400">Failed</p>
                  <p className="text-lg font-bold text-red-800 dark:text-red-300">{job.failed}</p>
                </div>
              </div>

              {isCompleted && job.output_url && (
                <Button
                  variant="success"
                  onClick={() => {
                    const base = import.meta.env.VITE_API_URL ?? ''
                    window.open(`${base}${job.output_url}`, '_blank')
                  }}
                >
                  <Download className="h-4 w-4" />
                  Download Results
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
    </div>
  )
}

export { MovieTitleBatchMatcher }
