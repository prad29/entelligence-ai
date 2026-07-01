import { useState } from 'react'
import { useAmenities, type Amenity } from '@/hooks/useAmenities'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Select'
import { AmenityFormDrawer } from './AmenityFormDrawer'
import { Plus, Pencil, Check, X, Download, Upload, Search } from 'lucide-react'
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
  active: 'success',
  pending: 'pending',
  inactive: 'default',
}

const statusOptions = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'pending', label: 'Pending' },
  { value: 'inactive', label: 'Inactive' },
]

const tierOptions = [
  { value: '', label: 'All tiers' },
  ...['P1', 'P2', 'P3', 'P4', 'P5', 'P6'].map((t) => ({ value: t, label: t })),
]

function AmenitiesPage() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<Amenity | null>(null)

  const { amenities, loading, createAmenity, updateAmenity, deleteAmenity } = useAmenities({
    search,
    status: statusFilter || undefined,
    tier: tierFilter || undefined,
  })

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
          {row.status === 'pending' && (
            <>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  void updateAmenity(row.id, { status: 'active' })
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
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search keywords or formats…"
            className="h-9 w-full rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 pl-9 pr-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-violet-500/30 focus:border-violet-500 transition-colors"
          />
        </div>
        <Select
          value={statusFilter}
          onValueChange={setStatusFilter}
          options={statusOptions}
          triggerClassName="w-36"
        />
        <Select
          value={tierFilter}
          onValueChange={setTierFilter}
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
          keyExtractor={(row) => row.id}
          emptyMessage="No amenity mappings found. Add one to get started."
        />
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
