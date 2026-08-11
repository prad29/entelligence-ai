import { useRef, useState } from 'react'
import {
  useDeletedShowtimeJob,
  DEFAULT_ADVANCED_OPTIONS,
  type DeletedShowtimeAdvancedOptions,
  type DeletedShowtimePreflight,
  type TheaterVerifyMode,
  type FallbackMode,
} from '@/hooks/useDeletedShowtimeJob'
import { Button } from '@/components/ui/Button'
import { Progress } from '@/components/ui/Progress'
import { Select } from '@/components/ui/Select'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import {
  UploadCloud,
  FileSpreadsheet,
  X,
  Download,
  CheckCircle2,
  AlertCircle,
  RotateCcw,
  ChevronDown,
  Settings2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { JobHistory } from './JobHistory'

const ACCEPTED_EXTENSIONS = '.csv,.xlsx'

const THEATER_VERIFY_OPTIONS = [
  { value: 'off', label: 'Off' },
  { value: 'warn', label: 'Warn (default)' },
  { value: 'strict', label: 'Strict' },
]

const FALLBACK_OPTIONS = [
  { value: 'off', label: 'Off' },
  { value: 'auto', label: 'Auto (default)' },
  { value: 'plain', label: 'Plain' },
  { value: 'movie', label: 'Movie' },
]

function ToggleRow({ label, description, checked, onChange }: {
  label: string
  description: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 dark:border-zinc-700 px-3.5 py-3 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800/40 transition-colors"
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-zinc-800 dark:text-zinc-100">{label}</p>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">{description}</p>
      </div>
      <div className={cn(
        'relative h-5 w-9 rounded-full transition-colors duration-200 shrink-0',
        checked ? 'bg-violet-500' : 'bg-zinc-300 dark:bg-zinc-600'
      )}>
        <span className={cn(
          'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
          checked ? 'translate-x-4' : 'translate-x-0'
        )} />
      </div>
    </button>
  )
}

function DeletedShowtimesCheck() {
  const [file, setFile] = useState<File | null>(null)
  const [options, setOptions] = useState<DeletedShowtimeAdvancedOptions>(DEFAULT_ADVANCED_OPTIONS)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [preflightResult, setPreflightResult] = useState<DeletedShowtimePreflight | null>(null)
  const [preflightChecking, setPreflightChecking] = useState(false)
  const [preflightError, setPreflightError] = useState<string | null>(null)
  const [historyKey, setHistoryKey] = useState(0)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const { job, uploading, isActive, resuming, error, preflight, uploadBatch, reset } = useDeletedShowtimeJob()

  const isCompleted = job?.status === 'completed'
  const isFailed = job?.status === 'failed'
  const isRunning = job?.status === 'queued' || job?.status === 'processing'
  const progressPct = job && job.total > 0 ? Math.round((job.processed / job.total) * 100) : 0

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (!selected) return
    setFile(selected)
    setPreflightResult(null)
    setPreflightError(null)
    setPreflightChecking(true)
    try {
      const result = await preflight(selected)
      setPreflightResult(result)
    } catch (e: unknown) {
      setPreflightError(e instanceof Error ? e.message : 'Could not read file')
    } finally {
      setPreflightChecking(false)
    }
  }

  const handleUpload = async () => {
    if (!file) return
    await uploadBatch(file, options)
    setHistoryKey((k) => k + 1)
  }

  const handleReset = () => {
    setFile(null)
    setPreflightResult(null)
    setPreflightError(null)
    setOptions(DEFAULT_ADVANCED_OPTIONS)
    setAdvancedOpen(false)
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
              <CardTitle>Deleted Showtimes Check</CardTitle>
              <CardDescription>
                Upload a CSV or XLSX export to verify showtimes still exist on Google via SerpApi.
                Requires <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">Theater Name</code>,{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">Title</code>,{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">Show date</code>, and{' '}
                <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-1 rounded">Show time</code> columns.
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
            <div className="flex flex-col gap-2">
              <label
                htmlFor="deleted-showtime-file"
                className={cn(
                  'relative border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-150 block',
                  'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50/50 dark:hover:bg-zinc-800/30'
                )}
              >
                <input
                  ref={fileInputRef}
                  id="deleted-showtime-file"
                  type="file"
                  accept={ACCEPTED_EXTENSIONS}
                  onChange={(e) => void handleFileChange(e)}
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
                        setPreflightResult(null)
                        setPreflightError(null)
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
                      Up to 1,000 rows per upload
                    </p>
                  </div>
                )}
              </label>

              {preflightChecking && (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">Checking file…</p>
              )}
              {preflightError && (
                <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {preflightError}
                </div>
              )}
              {preflightResult && preflightResult.rows_already_started > 0 && (
                <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-4 py-3 flex items-start gap-2 text-sm text-amber-800 dark:text-amber-300">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  <span>
                    {preflightResult.rows_already_started}/{preflightResult.row_count} rows have showtimes
                    already started in US Eastern time. Google no longer lists those, so they will come
                    back UNABLE_TO_DETERMINE instead of a real deletion signal. Re-run earlier for full
                    coverage, or continue anyway.
                  </span>
                </div>
              )}
            </div>
          )}

          {!job && !resuming && (
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setAdvancedOpen(!advancedOpen)}
                className="flex items-center gap-2 text-sm font-medium text-zinc-600 dark:text-zinc-300 self-start"
              >
                <Settings2 className="h-3.5 w-3.5" />
                Advanced options
                <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', advancedOpen && 'rotate-180')} />
              </button>

              {advancedOpen && (
                <div className="flex flex-col gap-3 rounded-xl border border-zinc-200 dark:border-zinc-700 p-4">
                  <ToggleRow
                    label="Title missing is deleted"
                    description="Treat a title fully absent from an otherwise healthy listing as TRUE (deleted) instead of UNABLE_TO_DETERMINE."
                    checked={options.titleMissingIsDeleted}
                    onChange={(v) => setOptions({ ...options, titleMissingIsDeleted: v })}
                  />
                  <ToggleRow
                    label="Strict screen count"
                    description="Treat a differing trailing screen count (e.g. 14 vs 16) as a different theater instead of the same venue renumbered."
                    checked={options.strictScreenCount}
                    onChange={(v) => setOptions({ ...options, strictScreenCount: v })}
                  />
                  <Select
                    label="Theater verify mode"
                    value={options.theaterVerify}
                    onValueChange={(v) => setOptions({ ...options, theaterVerify: v as TheaterVerifyMode })}
                    options={THEATER_VERIFY_OPTIONS}
                  />
                  <Select
                    label="Fallback query strategy"
                    value={options.fallback}
                    onValueChange={(v) => setOptions({ ...options, fallback: v as FallbackMode })}
                    options={FALLBACK_OPTIONS}
                  />
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                      Concurrency (parallel SerpApi calls)
                    </span>
                    <input
                      type="number"
                      min={1}
                      max={16}
                      value={options.workers}
                      onChange={(e) => setOptions({ ...options, workers: Number(e.target.value) || 1 })}
                      className="h-9 w-24 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-[#4A9FD4]/30 focus:border-[#4A9FD4]"
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {!job && !resuming && (
            <Button
              onClick={() => void handleUpload()}
              loading={uploading}
              disabled={!file || isActive || preflightChecking}
              className="w-full"
            >
              <UploadCloud className="h-4 w-4" />
              Run Deleted Showtimes Check
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
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {isCompleted && <CheckCircle2 className="h-5 w-5 text-emerald-500" />}
                  {isFailed && <AlertCircle className="h-5 w-5 text-red-500" />}
                  {isRunning && (
                    <div className="h-5 w-5 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
                  )}
                  <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50 capitalize">
                    {isRunning ? 'Checking showtimes…' : job.status}
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
                <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-center">
                  <p className="text-xs text-amber-600 dark:text-amber-400">Deleted (TRUE)</p>
                  <p className="text-lg font-bold text-amber-800 dark:text-amber-300">{job.true_count}</p>
                </div>
                <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/30 p-3 text-center">
                  <p className="text-xs text-emerald-600 dark:text-emerald-400">Confirmed (FALSE)</p>
                  <p className="text-lg font-bold text-emerald-800 dark:text-emerald-300">{job.false_count}</p>
                </div>
                <div className="rounded-lg bg-zinc-50 dark:bg-zinc-800 p-3 text-center">
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">Undetermined</p>
                  <p className="text-lg font-bold text-zinc-900 dark:text-zinc-50">{job.unknown_count}</p>
                </div>
              </div>

              {isCompleted && job.output_url && (
                <div className="flex gap-2">
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
                  {job.audit_url && (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        const base = import.meta.env.VITE_API_URL ?? ''
                        window.open(`${base}${job.audit_url}`, '_blank')
                      }}
                    >
                      <Download className="h-4 w-4" />
                      Download Audit JSON
                    </Button>
                  )}
                </div>
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

export { DeletedShowtimesCheck }
