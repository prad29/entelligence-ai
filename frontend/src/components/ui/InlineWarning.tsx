import { AlertCircle } from 'lucide-react'

interface InlineWarningProps {
  message: string
}

function InlineWarning({ message }: InlineWarningProps) {
  return (
    <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 px-4 py-3 flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400">
      <AlertCircle className="h-4 w-4 shrink-0" />
      {message}
    </div>
  )
}

export { InlineWarning }
