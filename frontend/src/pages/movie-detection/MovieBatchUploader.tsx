import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useMovieBatchJob } from '@/hooks/useMovieBatchJob'
import { Button } from '@/components/ui/Button'
import { Progress } from '@/components/ui/Progress'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Upload, FileText, X, Download, CheckCircle2, AlertCircle, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'

function MovieBatchUploader() {
  const [file, setFile] = useState<File | null>(null)
  const [includeDiagnostics, setIncludeDiagnostics] = useState(false)
  const [batchAiMode, setBatchAiMode] = useState<'skip' | 'sample' | 'full'>('skip')
  const [auditMode, setAuditMode] = useState(false)
  const { job, uploading, isActive, error, uploadBatch, reset } = useMovieBatchJob()

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted[0]) setFile(accepted[0])
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'text/csv': ['.csv'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    maxFiles: 1,
  })

  const handleUpload = async () => {
    if (!file) return
    await uploadBatch(file, includeDiagnostics, batchAiMode, auditMode)
  }

  const handleReset = () => {
    setFile(null)
    reset()
  }

  const isCompleted = job?.status === 'completed'
  const isFailed = job?.status === 'failed'
  const isRunning = job?.status === 'running' || job?.status === 'pending'

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-blue-600/10 dark:bg-blue-600/20 flex items-center justify-center">
              <Upload className="h-4 w-4 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <CardTitle>Batch Upload</CardTitle>
              <CardDescription>
                Upload a CSV or XLSX with an <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">amenities</code> column to detect movie formats in bulk. An optional <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">circuit_name</code> column is passed through to the output unchanged.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {/* Drop zone */}
          {!job && (
            <div
              {...getRootProps()}
              className={cn(
                'relative border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-150',
                isDragActive
                  ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/20'
                  : 'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50/50 dark:hover:bg-zinc-800/30'
              )}
            >
              <input {...getInputProps()} />
              {file ? (
                <div className="flex items-center justify-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center">
                    <FileText className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                  </div>
                  <div className="text-left">
                    <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{file.name}</p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">
                      {(file.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      setFile(null)
                    }}
                    className="ml-auto rounded-lg p-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <div className="h-12 w-12 rounded-xl bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center mb-1">
                    <Upload className="h-5 w-5 text-zinc-400" />
                  </div>
                  <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                    {isDragActive ? 'Drop your file here' : 'Drag & drop or click to browse'}
                  </p>
                  <p className="text-xs text-zinc-400 dark:text-zinc-500">CSV or XLSX — requires <code className="font-mono text-[11px]">amenities</code>; optional <code className="font-mono text-[11px]">circuit_name</code> passes through</p>
                </div>
              )}
            </div>
          )}

          {/* Diagnostics toggle */}
          {!job && (
            <label className="flex items-center gap-3 cursor-pointer group">
              <div className="relative">
                <input
                  type="checkbox"
                  checked={includeDiagnostics}
                  onChange={(e) => setIncludeDiagnostics(e.target.checked)}
                  className="sr-only peer"
                />
                <div className={cn(
                  'h-5 w-9 rounded-full border-2 transition-colors duration-200',
                  includeDiagnostics
                    ? 'bg-violet-600 border-violet-600'
                    : 'bg-zinc-200 dark:bg-zinc-700 border-zinc-200 dark:border-zinc-700'
                )}>
                  <div className={cn(
                    'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
                    includeDiagnostics ? 'translate-x-4' : 'translate-x-0'
                  )} />
                </div>
              </div>
              <span className="text-sm text-zinc-700 dark:text-zinc-300">Include diagnostics in output</span>
            </label>
          )}

          {/* AI Mode selector */}
          {!job && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400">AI mode</p>
              <div className="flex gap-2">
                {([
                  { value: 'skip', label: 'Skip AI', desc: 'Fast — no Bedrock calls, unmatched → 2D' },
                  { value: 'sample', label: 'Sample', desc: 'Balanced — AI for first 50 unique no-matches' },
                  { value: 'full', label: 'Full AI', desc: 'Slow — Bedrock call for every no-match' },
                ] as const).map(({ value, label, desc }) => (
                  <button
                    key={value}
                    onClick={() => setBatchAiMode(value)}
                    className={cn(
                      'flex-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors',
                      batchAiMode === value
                        ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                        : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600'
                    )}
                  >
                    <span className="font-semibold block">{label}</span>
                    <span className="text-zinc-400 dark:text-zinc-500">{desc}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Audit mode toggle */}
          {!job && (
            <div className="flex flex-col gap-1.5">
              <label className="flex items-center gap-3 cursor-pointer group">
                <div className="relative">
                  <input
                    type="checkbox"
                    checked={auditMode}
                    onChange={(e) => setAuditMode(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className={cn(
                    'h-5 w-9 rounded-full border-2 transition-colors duration-200',
                    auditMode
                      ? 'bg-violet-600 border-violet-600'
                      : 'bg-zinc-200 dark:bg-zinc-700 border-zinc-200 dark:border-zinc-700'
                  )}>
                    <div className={cn(
                      'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-200',
                      auditMode ? 'translate-x-4' : 'translate-x-0'
                    )} />
                  </div>
                </div>
                <span className="text-sm text-zinc-700 dark:text-zinc-300">Audit mode</span>
              </label>
              {auditMode && (
                <div className="ml-12 flex flex-col gap-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  <p>CSV must include: <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">amenities</code> (or <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">amenities_string</code>), <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">movie_format</code></p>
                  <p>Output adds: <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">detected_format</code>, <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">anomaly</code>, <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">ai_suggested_format</code>, <code className="font-mono text-[11px] bg-zinc-100 dark:bg-zinc-800 px-1 rounded">reasoning</code></p>
                </div>
              )}
            </div>
          )}

          {/* Upload button */}
          {!job && (
            <Button
              onClick={() => void handleUpload()}
              loading={uploading}
              disabled={!file || isActive}
              className="w-full"
            >
              <Upload className="h-4 w-4" />
              Start Batch Detection
            </Button>
          )}

          {/* Error */}
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
                <Button variant="ghost" size="sm" onClick={handleReset}>
                  <RotateCcw className="h-3.5 w-3.5" />
                  New batch
                </Button>
              </div>

              <div className="flex flex-col gap-1.5">
                <Progress
                  value={job.progress * 100}
                  indicatorClassName={isCompleted ? 'bg-emerald-500' : isFailed ? 'bg-red-500' : 'bg-violet-600'}
                />
                <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
                  <span>{Math.round(job.progress * 100)}%</span>
                  <span>{job.processed} / {job.total} rows</span>
                </div>
              </div>

              <div className={cn('grid gap-3', auditMode && job.anomaly_count != null ? 'grid-cols-5' : 'grid-cols-4')}>
                <div className="rounded-lg bg-zinc-50 dark:bg-zinc-800 p-3 text-center">
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">Total</p>
                  <p className="text-lg font-bold text-zinc-900 dark:text-zinc-50">{job.total}</p>
                </div>
                <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 p-3 text-center">
                  <p className="text-xs text-emerald-600 dark:text-emerald-400">Keyword Match</p>
                  <p className="text-lg font-bold text-emerald-800 dark:text-emerald-300">{job.matched}</p>
                </div>
                <div className="rounded-lg bg-zinc-50 dark:bg-zinc-800 p-3 text-center">
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">No Match → 2D</p>
                  <p className="text-lg font-bold text-zinc-900 dark:text-zinc-50">{job.no_match ?? (job.total - job.matched - job.ai_suggestions)}</p>
                </div>
                <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-center">
                  <p className="text-xs text-amber-600 dark:text-amber-400">AI Classified</p>
                  <p className="text-lg font-bold text-amber-800 dark:text-amber-300">{job.ai_suggestions}</p>
                </div>
                {auditMode && job.anomaly_count != null && (
                  <div className="rounded-lg bg-red-50 dark:bg-red-950/30 p-3 text-center">
                    <p className="text-xs text-red-600 dark:text-red-400">Anomalies</p>
                    <p className="text-lg font-bold text-red-800 dark:text-red-300">{job.anomaly_count}</p>
                  </div>
                )}
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
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export { MovieBatchUploader }
