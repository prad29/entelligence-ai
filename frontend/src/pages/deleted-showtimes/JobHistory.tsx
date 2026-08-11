import { useEffect, useState } from 'react'
import api from '@/lib/api'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Download } from 'lucide-react'
import type { DeletedShowtimeJob } from '@/hooks/useDeletedShowtimeJob'

interface JobHistoryProps {
  refreshKey: number
}

const STATUS_VARIANT: Record<string, 'success' | 'danger' | 'warning' | 'default'> = {
  completed: 'success',
  failed: 'danger',
  processing: 'warning',
  queued: 'default',
}

function formatDate(iso?: string): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function JobHistory({ refreshKey }: JobHistoryProps) {
  const [jobs, setJobs] = useState<DeletedShowtimeJob[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .get<{ jobs: DeletedShowtimeJob[] }>('/api/v1/deleted-showtimes/jobs')
      .then((res) => {
        if (!cancelled) setJobs(res.data.jobs)
      })
      .catch(() => {
        if (!cancelled) setJobs([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  const columns: Column<DeletedShowtimeJob>[] = [
    { key: 'original_filename', header: 'File', cell: (row) => row.original_filename || '—' },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => (
        <Badge variant={STATUS_VARIANT[row.status] ?? 'default'} className="capitalize">
          {row.status}
        </Badge>
      ),
    },
    { key: 'total', header: 'Rows', cell: (row) => String(row.total) },
    { key: 'true_count', header: 'Deleted', cell: (row) => String(row.true_count) },
    { key: 'false_count', header: 'Confirmed', cell: (row) => String(row.false_count) },
    { key: 'unknown_count', header: 'Undetermined', cell: (row) => String(row.unknown_count) },
    { key: 'created_at', header: 'Started', cell: (row) => formatDate(row.created_at) },
    {
      key: 'download',
      header: '',
      cell: (row) =>
        row.output_url ? (
          <a
            href={`${import.meta.env.VITE_API_URL ?? ''}${row.output_url}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-[#4A9FD4] hover:underline"
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </a>
        ) : (
          <span className="text-xs text-zinc-400">—</span>
        ),
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run History</CardTitle>
        <CardDescription>Past Deleted Showtimes Check runs, across all users.</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          data={jobs}
          keyExtractor={(row) => row.job_id}
          emptyMessage={loading ? 'Loading…' : 'No runs yet.'}
        />
      </CardContent>
    </Card>
  )
}

export { JobHistory }
