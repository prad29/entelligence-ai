import { useState } from 'react'
import { useAmenities, type Amenity } from '@/hooks/useAmenities'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'
import { AmenityFormDrawer } from './AmenityFormDrawer'
import { Plus, Pencil, Check, X, Download, Upload, Search, ChevronLeft, ChevronRight } from 'lucide-react'
import { formatDate } from '@/lib/utils'

const tierVariantMap: Record<string, BadgeVariant> = {
  P1: 'p1',
  P2: 'p2',
  P3: 'p3',
  P4: 'p4',
  P5: 'p5',
  P6: 'p6',
}

const statusVariantMap: Record<string, BadgeVariant> = {
  approved: 'success',
  pending: 'pending',
  rejected: 'danger',
  draft: 'default',
}

const statusOptions = [
  { value: '', label: 'All statuses' },
  { value: 'approved', label: 'Approved' },
  { value: 'pending', label: 'Pending' },
  { value: 'rejected', label: 'Rejected' },
]

const tierOptions = [
  { value: '', label: 'All tiers' },
  ...['P1', 'P2', 'P3', 'P4', 'P5', 'P6'].map((t) => ({ value: t, label: t })),
]

function AmenitiesPage() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [page, setPage] = useState(1)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<Amenity | null>(null)

  const { amenities, total, totalPages, loading, createAmenity, updateAmenity, deleteAmenity } = useAmenities({
    search,
    status: statusFilter || undefined,
    tier: tierFilter || undefined,
    page,
  })

  const resetPage = () => setPage(1)

  const columns: Column<Amenity>[] = [
    {
      key: 'keyword',
      header: 'Keyword',
      sortable: true,
      cell: (row) => (
        <span className="font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-100">
          {row.keyword}
        </span>
      ),
    },
    {
      key: 'screen_format',
      header: 'Screen Format',
      sortable: true,
      cell: (row) => (
        <span className="font-medium text-zinc-800 dark:text-zinc-200">{row.screen_format}</span>
      ),
    },
    {
      key: 'tier',
      header: 'Tier',
      sortable: true,
      cell: (row) => <Badge variant={tierVariantMap[row.tier] ?? 'default'}>{row.tier}</Badge>,
    },
    {
      key: 'circuit',
      header: 'Circuit',
      cell: (row) => (
        <span className="text-zinc-500 dark:text-zinc-400 text-xs">
          {row.circuit ?? <span className="italic text-zinc-300 dark:text-zinc-600">Global</span>}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => (
        <Badge variant={statusVariantMap[row.status] ?? 'default'}>
          {row.status}
        </Badge>
      ),
    },
    {
      key: 'updated_at',
      header: 'Updated',
      sortable: true,
      cell: (row) => (
        <span className="text-xs text-zinc-400 dark:text-zinc-500">{formatDate(row.updated_at)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      cell: (row) => (
        <div className="flex items-center gap-1 justify-end">
          {(row.status === 'pending' || row.status === 'draft') && (
            <>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  void updateAmenity(row.id, { status: 'approved' })
                }}
                className="rounded-md p-1.5 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/30 transition-colors"
                title="Approve"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  void updateAmenity(row.id, { status: 'inactive' })
                }}
                className="rounded-md p-1.5 text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
                title="Reject"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation()
              setEditTarget(row)
              setDrawerOpen(true)
            }}
            className="rounded-md p-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            title="Edit"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              void deleteAmenity(row.id)
            }}
            className="rounded-md p-1.5 text-zinc-300 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
            title="Delete"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ),
    },
  ]

  const handleAdd = () => {
    setEditTarget(null)
    setDrawerOpen(true)
  }

  const handleSubmit = async (data: Omit<Amenity, 'id' | 'updated_at'>) => {
    if (editTarget) {
      await updateAmenity(editTarget.id, data)
    } else {
      await createAmenity(data)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-400 pointer-events-none" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); resetPage() }}
            placeholder="Search keywords or formats…"
            className="h-9 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 pl-9 pr-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-violet-500/30 focus:border-violet-500 transition-colors"
          />
        </div>
        <Select
          value={statusFilter}
          onValueChange={(v) => { setStatusFilter(v); resetPage() }}
          options={statusOptions}
          triggerClassName="w-36"
        />
        <Select
          value={tierFilter}
          onValueChange={(v) => { setTierFilter(v); resetPage() }}
          options={tierOptions}
          triggerClassName="w-28"
        />
        <div className="flex items-center gap-1.5 ml-auto">
          <Button variant="secondary" size="sm">
            <Upload className="h-3.5 w-3.5" />
            Import
          </Button>
          <Button variant="secondary" size="sm">
            <Download className="h-3.5 w-3.5" />
            Export
          </Button>
          <Button size="sm" onClick={handleAdd}>
            <Plus className="h-3.5 w-3.5" />
            Add Mapping
          </Button>
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center h-64 text-zinc-400">
          <div className="h-6 w-6 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={amenities}
          keyExtractor={(row) => String(row.id)}
          emptyMessage="No amenity mappings found. Add one to get started."
        />
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-zinc-500 dark:text-zinc-400">
          <span>{total} total</span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="rounded-md p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter((p) => p === 1 || p === totalPages || Math.abs(p - page) <= 2)
              .reduce<(number | '…')[]>((acc, p, idx, arr) => {
                if (idx > 0 && p - (arr[idx - 1] as number) > 1) acc.push('…')
                acc.push(p)
                return acc
              }, [])
              .map((p, idx) =>
                p === '…' ? (
                  <span key={`ellipsis-${idx}`} className="px-1">…</span>
                ) : (
                  <button
                    key={p}
                    onClick={() => setPage(p as number)}
                    className={`min-w-[2rem] rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                      page === p
                        ? 'bg-[#4A9FD4] text-white'
                        : 'hover:bg-zinc-100 dark:hover:bg-zinc-800'
                    }`}
                  >
                    {p}
                  </button>
                )
              )}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="rounded-md p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <AmenityFormDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        amenity={editTarget}
        onSubmit={handleSubmit}
      />
    </div>
  )
}

export { AmenitiesPage }
