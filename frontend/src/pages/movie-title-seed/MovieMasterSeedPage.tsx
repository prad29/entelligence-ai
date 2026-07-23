import { useRef, useState, useEffect, useCallback } from 'react'
import { Upload, CheckCircle2, AlertCircle, Database, FileSpreadsheet, Loader2, Globe2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMovieMasterSeed, type MovieMasterMarket } from '@/hooks/useMovieMasterSeed'

function StatBadge({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className={cn(
      'flex flex-col items-center justify-center rounded-xl px-5 py-3 min-w-[100px]',
      accent ? 'bg-[#4A9FD4]/10 ring-1 ring-[#4A9FD4]/30' : 'bg-zinc-100 dark:bg-zinc-800'
    )}>
      <span className={cn('text-2xl font-bold tabular-nums', accent ? 'text-[#4A9FD4]' : 'text-zinc-800 dark:text-zinc-100')}>
        {value.toLocaleString()}
      </span>
      <span className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">{label}</span>
    </div>
  )
}

function DropZone({ onFile, disabled }: { onFile: (f: File) => void; disabled: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }, [onFile])

  return (
    <div
      className={cn(
        'relative flex flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed transition-all cursor-pointer select-none px-8 py-14',
        dragging
          ? 'border-[#4A9FD4] bg-[#4A9FD4]/5'
          : 'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600 bg-zinc-50 dark:bg-zinc-900',
        disabled && 'pointer-events-none opacity-50'
      )}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white dark:bg-zinc-800 shadow-sm ring-1 ring-zinc-200 dark:ring-zinc-700">
        <FileSpreadsheet className="h-7 w-7 text-[#4A9FD4]" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
          Drop your file here, or <span className="text-[#4A9FD4]">browse</span>
        </p>
        <p className="mt-1 text-xs text-zinc-400">.csv or .xlsx — up to 500k rows</p>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        className="sr-only"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f) }}
      />
    </div>
  )
}

function MarketToggle({ market, onChange }: { market: MovieMasterMarket; onChange: (m: MovieMasterMarket) => void }) {
  const isIntl = market === 'international'
  return (
    <button
      type="button"
      onClick={() => onChange(isIntl ? 'domestic' : 'international')}
      className={cn(
        'flex items-center gap-3 rounded-xl border px-4 py-3 text-sm transition-all duration-200 w-full text-left',
        isIntl
          ? 'border-[#4A9FD4] dark:border-[#4A9FD4]/70 bg-[#4A9FD4]/5 dark:bg-[#4A9FD4]/10 text-[#4A9FD4]'
          : 'border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800/40'
      )}
    >
      {/* Track */}
      <div className={cn(
        'relative h-5 w-9 rounded-full transition-colors duration-200 shrink-0',
        isIntl ? 'bg-[#4A9FD4]' : 'bg-zinc-300 dark:bg-zinc-600'
      )}>
        {/* Thumb */}
        <span className={cn(
          'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200',
          isIntl ? 'translate-x-4' : 'translate-x-0'
        )} />
      </div>
      <Globe2 className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">International</span>
      <span className="ml-auto text-xs opacity-60 shrink-0">
        {isIntl ? 'Seeding Movie Master International' : 'Seeding Movie Master Domestic'}
      </span>
    </button>
  )
}

function MovieMasterSeedPage() {
  const [market, setMarket] = useState<MovieMasterMarket>('domestic')
  const { loading, result, error, uploadFile, fetchCount, reset } = useMovieMasterSeed(market)
  const [currentCount, setCurrentCount] = useState<number | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  useEffect(() => {
    fetchCount().then(setCurrentCount)
  }, [market])

  function handleMarketChange(next: MovieMasterMarket) {
    setMarket(next)
    setSelectedFile(null)
    reset()
  }

  function handleFile(file: File) {
    setSelectedFile(file)
    reset()
  }

  async function handleUpload() {
    if (!selectedFile) return
    await uploadFile(selectedFile)
    const updated = await fetchCount()
    setCurrentCount(updated)
  }

  const isIntl = market === 'international'

  return (
    <div className="max-w-2xl mx-auto flex flex-col gap-6 py-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#4A9FD4]/10 ring-1 ring-[#4A9FD4]/20">
          <Database className="h-5 w-5 text-[#4A9FD4]" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Seed Movie Master {isIntl && '(International)'}
          </h1>
          <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">
            {isIntl
              ? 'Upload the Movie Master International CSV or Excel file to populate the database. Rows are upserted by (movie_id, country, release_date).'
              : 'Upload the Movie Master CSV or Excel file to populate the database. New entries are inserted; existing entries are updated by ID.'}
            {currentCount === 0 && (
              <span className="ml-1 font-medium text-amber-600 dark:text-amber-400">
                Table is currently empty — full seed will run.
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Domestic / International toggle */}
      <MarketToggle market={market} onChange={handleMarketChange} />

      {/* Current count pill */}
      {currentCount !== null && (
        <div className="flex items-center gap-2 rounded-lg bg-zinc-100 dark:bg-zinc-800 px-4 py-2.5 text-sm">
          <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
          <span className="text-zinc-600 dark:text-zinc-300">
            Currently <span className="font-semibold text-zinc-900 dark:text-zinc-50">{currentCount.toLocaleString()}</span> movies in the database
          </span>
        </div>
      )}

      {/* Drop zone */}
      <DropZone onFile={handleFile} disabled={loading} />

      {/* Selected file + upload button */}
      {selectedFile && !result && (
        <div className="flex items-center gap-3 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-3">
          <FileSpreadsheet className="h-5 w-5 shrink-0 text-zinc-400" />
          <div className="flex-1 min-w-0">
            <p className="truncate text-sm font-medium text-zinc-700 dark:text-zinc-200">{selectedFile.name}</p>
            <p className="text-xs text-zinc-400">{(selectedFile.size / 1024 / 1024).toFixed(2)} MB</p>
          </div>
          <button
            onClick={handleUpload}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg bg-[#4A9FD4] px-4 py-2 text-sm font-medium text-white hover:bg-[#3A8FC4] disabled:opacity-60 transition-colors shrink-0"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Seeding…
              </>
            ) : (
              <>
                <Upload className="h-4 w-4" />
                Seed Database
              </>
            )}
          </button>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-3 rounded-xl border border-[#4A9FD4]/20 bg-[#4A9FD4]/5 px-4 py-3 text-sm text-[#4A9FD4]">
          <Loader2 className="h-4 w-4 animate-spin shrink-0" />
          Processing file — this may take a moment for large datasets…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 px-4 py-3">
          <AlertCircle className="h-5 w-5 shrink-0 text-red-500 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-red-700 dark:text-red-400">Seed failed</p>
            <p className="mt-0.5 text-xs text-red-600 dark:text-red-500">{error}</p>
          </div>
        </div>
      )}

      {/* Success result */}
      {result && (
        <div className="flex flex-col gap-4 rounded-2xl border border-emerald-200 dark:border-emerald-800/50 bg-emerald-50 dark:bg-emerald-950/20 px-5 py-5">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">Seed complete</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <StatBadge label="Inserted" value={result.inserted} accent />
            <StatBadge label="Updated" value={result.updated} />
            <StatBadge label="Skipped" value={result.skipped} />
            {isIntl && (
              <StatBadge label="Skipped (no country)" value={result.skipped_undefined_country ?? 0} />
            )}
            <StatBadge label="In file" value={result.total_in_file} />
          </div>
          {result.previously_seeded === 0 && result.inserted > 0 && (
            <p className="text-xs text-emerald-600 dark:text-emerald-400">
              Full seed completed — table was empty before upload.
            </p>
          )}
          <button
            onClick={() => { reset(); setSelectedFile(null) }}
            className="self-start rounded-lg border border-emerald-300 dark:border-emerald-700 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-900/30 transition-colors"
          >
            Upload another file
          </button>
        </div>
      )}
    </div>
  )
}

export { MovieMasterSeedPage }
