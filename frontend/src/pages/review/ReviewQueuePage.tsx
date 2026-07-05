import { useState, useEffect, useCallback } from 'react'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Badge, type BadgeVariant } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Textarea } from '@/components/ui/Textarea'
import { Progress } from '@/components/ui/Progress'
import { Check, X, CheckCheck, ChevronDown, ChevronUp } from 'lucide-react'
import api from '@/lib/api'

function ExpandableText({ text, mono = false, threshold = 60 }: { text: string; mono?: boolean; threshold?: number }) {
  const [expanded, setExpanded] = useState(false)
  const isLong = text.length > threshold

  return (
    <div className="max-w-xs">
      <p className={`text-xs leading-relaxed ${mono ? 'font-mono text-zinc-700 dark:text-zinc-300' : 'text-zinc-500 dark:text-zinc-400'} ${!expanded && isLong ? 'line-clamp-2' : ''}`}>
        {text}
      </p>
      {isLong && (
        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
          className="mt-0.5 inline-flex items-center gap-0.5 text-[11px] text-[#4A9FD4] hover:text-[#3a8fc4] font-medium"
        >
          {expanded ? <><ChevronUp className="h-3 w-3" /> Less</> : <><ChevronDown className="h-3 w-3" /> More</>}
        </button>
      )}
    </div>
  )
}

interface ReviewItem {
  id: number
  type: 'mapping' | 'ai_suggestion' | 'circuit_override'
  source_string: string | null
  circuit: string | null
  suggested_format: string | null
  confidence: number | null
  reasoning: string | null
  status: 'pending' | 'approved' | 'rejected'
  decided_at: string | null
}

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
  onApprove: (id: number) => void
  onReject: (id: number, reason: string) => void
}) {
  const [rejectId, setRejectId] = useState<number | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const handleBulkApprove = () => {
    selectedIds.forEach((id) => onApprove(Number(id)))
    setSelectedIds(new Set())
  }

  const columns: Column<ReviewItem>[] = [
    {
      key: 'type',
      header: 'Type',
      cell: (row) => (
        <Badge variant={row.type === 'ai_suggestion' ? 'warning' : 'info'} size="sm">
          {row.type === 'ai_suggestion' ? 'AI' : row.type === 'circuit_override' ? 'Circuit' : 'Mapping'}
        </Badge>
      ),
    },
    {
      key: 'source_string',
      header: 'Source String',
      cell: (row) => row.source_string
        ? <ExpandableText text={row.source_string} mono threshold={40} />
        : <span className="text-xs text-zinc-400">—</span>,
    },
    {
      key: 'suggested_format',
      header: 'Suggested Format',
      sortable: true,
      cell: (row) => (
        <span className="font-semibold text-zinc-900 dark:text-zinc-100">{row.suggested_format ?? '—'}</span>
      ),
    },
    {
      key: 'reasoning',
      header: 'Reasoning',
      cell: (row) => row.reasoning ? <ExpandableText text={row.reasoning} threshold={80} /> : null,
    },
    {
      key: 'confidence',
      header: 'Confidence',
      cell: (row) => row.confidence != null ? (
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
      ) : <span className="text-xs text-zinc-300 dark:text-zinc-600">—</span>,
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
        keyExtractor={(r) => String(r.id)}
        selectable
        selectedIds={selectedIds}
        onSelectionChange={setSelectedIds}
        emptyMessage="No items in queue."
      />

      <RejectModal
        open={rejectId !== null}
        onOpenChange={(open) => { if (!open) setRejectId(null) }}
        onConfirm={(reason) => {
          if (rejectId != null) { onReject(rejectId, reason); setRejectId(null) }
        }}
      />
    </div>
  )
}

function ReviewQueuePage() {
  const [items, setItems] = useState<ReviewItem[]>([])
  const [loading, setLoading] = useState(true)

  const fetchItems = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get<ReviewItem[]>('/api/v1/review?limit=200')
      setItems(res.data)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void fetchItems() }, [fetchItems])

  const approve = async (id: number) => {
    await api.post(`/api/v1/review/${id}/approve`, {})
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: 'approved' as const } : i)))
  }

  const reject = async (id: number, reason: string) => {
    await api.post(`/api/v1/review/${id}/reject`, { reason })
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, status: 'rejected' as const } : i)))
  }

  const pending = items.filter((i) => i.status === 'pending')
  const aiSuggestions = items.filter((i) => i.type === 'ai_suggestion')

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400">
        <div className="h-6 w-6 rounded-full border-2 border-violet-600 border-t-transparent animate-spin" />
      </div>
    )
  }

  return (
    <Tabs
      defaultValue="all"
      tabs={[
        { value: 'all', label: 'All', count: items.length },
        { value: 'pending', label: 'Pending', count: pending.length },
        { value: 'ai', label: 'AI Suggestions', count: aiSuggestions.length },
      ]}
    >
      <TabsContent value="all">
        <ReviewTable items={items} onApprove={approve} onReject={reject} />
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
