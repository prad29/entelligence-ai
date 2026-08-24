import { Download } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { useUsageReport, type UsageFilterState } from '@/hooks/useUsage'

interface ReportDownloadButtonsProps {
  filters: UsageFilterState
  /** true while rangeError is set — a report must never fire against an
   *  invalid range either. */
  disabled?: boolean
}

/** Uses the blob-download hook rather than `window.open`
 *  (`DeletedShowtimesCheck.tsx:352`'s pattern): opening a 400 response in a
 *  new tab shows a blank tab with raw JSON in it. The blob path keeps
 *  failures inside the UI, and report generation is the one place a
 *  range/filter problem is most likely to surface. It also honours the
 *  backend's `Content-Disposition` filename
 *  (`usage-report-<start>-to-<end>.<ext>`, `routers/usage.py:213`).
 *
 *  The report is built by `report.collect_report()` from the same
 *  `queries.*` functions the dashboard calls, with the same filter object —
 *  so the file can never disagree with the screen, and always includes a
 *  full `granularity=day` series and the `task_type`/`model_id` breakdowns
 *  regardless of which dimension the detail table is currently showing. */
function ReportDownloadButtons({ filters, disabled }: ReportDownloadButtonsProps) {
  const { downloading, error, download } = useUsageReport()

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="secondary"
        size="sm"
        disabled={disabled || downloading !== null}
        loading={downloading === 'csv'}
        onClick={() => void download('csv', filters)}
      >
        <Download className="h-3.5 w-3.5" />
        CSV
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={disabled || downloading !== null}
        loading={downloading === 'pdf'}
        onClick={() => void download('pdf', filters)}
      >
        <Download className="h-3.5 w-3.5" />
        PDF
      </Button>
      {error && <span className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  )
}

export { ReportDownloadButtons }
