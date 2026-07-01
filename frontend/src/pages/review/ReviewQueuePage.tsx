import { useState } from 'react'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Textarea } from '@/components/ui/Textarea'
import { Progress } from '@/components/ui/Progress'
import { Check, X, CheckCheck } from 'lucide-react'
import { truncate, formatDate } from '@/lib/utils'

interface ReviewItem {
  id: string
  type: 'mapping' | 'ai_suggestion'
  source_string: string
  suggested_format: string
  confidence: number
  created_at: string
  status: 'pending' | 'approved' | 'rejected'
  reject_reason?: string
}

const SAMPLE_ITEMS: ReviewItem[] = [
  { id: '1', type: 'ai_suggestion', source_string: 'CINÉ XL® | Dolby Atmos', suggested_format: 'CINÉ XL', confidence: 0.82, created_at: '2025-06-15T10:30:00Z', status: 'pending' },
  { id: '2', type: 'mapping', source_string: 'VIP | Ultra Screen', suggested_format: 'VIP Ultra', confidence: 0.91, created_at: '2025-06-14T08:15:00Z', status: 'pending' },
  { id: '3', type: 'ai_suggestion', source_string: 'Laser IMAX | Recliners', suggested_format: 'IMAX', confidence: 0.95, created_at: '2025-06-13T14:00:00Z', status: 'approved' },
  { id: '4', type: 'mapping', source_string: 'BTX | D-BOX', suggested_format: 'BTX', confidence: 0.77, created_at: '2025-06-12T11:45:00Z', status: 'pending' },
  { id: '5', type: 'ai_suggestion', source_string: 'XD Extreme Digital', suggested_format: 'XD', confidence: 0.88, created_at: '2025-06-11T09:00:00Z', status: 'rejected', reject_reason: 'Too generic' },
]

interface RejectModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (reason: string) => void
  loading?: boolean
}

function RejectModal({ open, onOpenChange, onConfirm, loading }: RejectModalProps) {
  const [reason, setReason] = useState('')

  const handleConfirm = () => {
    onConfirm(reason)
    setReason('')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Reject Suggestion" description="Provide a reason for rejection (optional).">
      <div className="flex flex-col gap-4">
        <Textarea
          placeholder="e.g. Ambiguous format, needs more context…"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
        />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="danger" onClick={handleConfirm} loading={loading}>
            <X className="h-4 w-4" />
            Reject
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function ReviewTable({ items, onApprove, onReject }: {
  items: ReviewItem[]
  onApprove: (id: string) => void
  onReject: (id: string, reason: string) => void
}) {
  const [rejectId, setRejectId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const pendingItems = items.filter((i) => i.status === 'pending')

  const handleBulkApprove = () => {
    selectedIds.forEach((id) => onApprove(id))
    setSelectedIds(new Set())
  }

  const columns: Column<ReviewItem>[] = [
    {
      key: 'type',
      header: 'Type',
      cell: (row) => (
        <Badge variant={row.type === 'ai_suggestion' ? 'warning' : 'info'} size="sm">
          {row.type === 'ai_suggestion' ? 'AI' : 'Mapping'}
        </Badge>
      ),
    },
    {
      key: 'source_string',
      header: 'Source String',
      cell: (row) => (
        <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300" title={row.source_string}>
          {truncate(row.source_string, 40)}
        </span>
      ),
    },
    {
      key: 'suggested_format',
      header: 'Suggested Format',
      sortable: true,
      cell: (row) => (
        <span className="font-semibold text-zinc-900 dark:text-zinc-100">{row.suggested_format}</span>
      ),
    },
    {
      key: 'confidence',
      header: 'Confidence',
      cell: (row) => (
        <div className="flex items-center gap-2 min-w-28">
          <Progress
            value={row.confidence * 100}
            className="h-1.5 flex-1"
            indicatorClassName={
              row.confidence >= 0.9 ? 'bg-emerald-500' :
              row.confidence >= 0.75 ? 'bg-amber-500' : 'bg-red-500'
            }
          />
          <span className="text-xs font-mono text-zinc-500 dark:text-zinc-400 shrink-0">
            {Math.round(row.confidence * 100)}%
          </span>
        </div>
      ),
    },
    {
      key: 'created_at',
      header: 'Created',
      sortable: true,
      cell: (row) => (
        <span className="text-xs text-zinc-400 dark:text-zinc-500">{formatDate(row.created_at)}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => {
        const v: BadgeVariant = row.status === 'approved' ? 'success' : row.status === 'rejected' ? 'danger' : 'pending'
        return <Badge variant={v}>{row.status}</Badge>
      },
    },
    {
      key: 'actions',
      header: '',
      cell: (row) => row.status === 'pending' ? (
        <div className="flex items-center gap-1 justify-end">
          <button
            onClick={(e) => { e.stopPropagation(); onApprove(row.id) }}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/30 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors"
          >
            <Check className="h-3 w-3" /> Approve
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setRejectId(row.id) }}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors"
          >
            <X className="h-3 w-3" /> Reject
          </button>
        </div>
      ) : null,
    },
  ]

  return (
    <div className="flex flex-col gap-3">
      {/* Bulk actions bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 rounded-xl bg-violet-50 dark:bg-violet-950/30 border border-violet-200 dark:border-violet-800 px-4 py-2.5">
          <span className="text-sm font-medium text-violet-700 dark:text-violet-300">
            {selectedIds.size} selected
          </span>
          <Button size="sm" variant="primary" onClick={handleBulkApprove}>
            <CheckCheck className="h-3.5 w-3.5" />
            Approve All
          </Button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="ml-auto text-xs text-violet-500 dark:text-violet-400 hover:underline"
          >
            Clear selection
          </button>
        </div>
      )}

      <DataTable
        columns={columns}
        data={items}
        keyExtractor={(r) => r.id}
        selectable
        selectedIds={selectedIds}
        onSelectionChange={setSelectedIds}
        emptyMessage="No items in queue."
      />

      <RejectModal
        open={rejectId !== null}
        onOpenChange={(open) => { if (!open) setRejectId(null) }}
        onConfirm={(reason) => {
          if (rejectId) { onReject(rejectId, reason); setRejectId(null) }
        }}
      />

      {/* suppress unused pendingItems warning */}
      <span className="hidden">{pendingItems.length}</span>
    </div>
  )
}

function ReviewQueuePage() {
  const [items, setItems] = useState<ReviewItem[]>(SAMPLE_ITEMS)

  const approve = (id: string) => {
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: 'approved' as const } : i)))
  }

  const reject = (id: string, reason: string) => {
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: 'rejected' as const, reject_reason: reason } : i)))
  }

  const all = items
  const pending = items.filter((i) => i.status === 'pending')
  const aiSuggestions = items.filter((i) => i.type === 'ai_suggestion')

  return (
    <Tabs
      defaultValue="all"
      tabs={[
        { value: 'all', label: 'All', count: all.length },
        { value: 'pending', label: 'Pending', count: pending.length },
        { value: 'ai', label: 'AI Suggestions', count: aiSuggestions.length },
      ]}
    >
      <TabsContent value="all">
        <ReviewTable items={all} onApprove={approve} onReject={reject} />
      </TabsContent>
      <TabsContent value="pending">
        <ReviewTable items={pending} onApprove={approve} onReject={reject} />
      </TabsContent>
      <TabsContent value="ai">
        <ReviewTable items={aiSuggestions} onApprove={approve} onReject={reject} />
      </TabsContent>
    </Tabs>
  )
}

export { ReviewQueuePage }
