import { AlertCircle } from 'lucide-react'

interface InlineErrorProps {
  message: string
}

function InlineError({ message }: InlineErrorProps) {
  return (
    <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 flex items-center gap-2 text-sm text-red-700 dark:text-red-400">
      <AlertCircle className="h-4 w-4 shrink-0" />
      {message}
    </div>
  )
}

export { InlineError }
