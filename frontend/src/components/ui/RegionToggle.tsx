import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { cn } from '@/lib/utils'

interface RegionToggleProps {
  domesticPath: string
  intlPath: string
}

function RegionToggle({ domesticPath, intlPath }: RegionToggleProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const isIntl = location.pathname.startsWith(intlPath)
  const isDomestic = !isIntl && location.pathname.startsWith(domesticPath)

  const navigateTo = (path: string) => {
    const tab = searchParams.get('tab')
    const query = tab ? `?tab=${tab}` : ''
    navigate(`${path}${query}`)
  }

  return (
    <div
      role="group"
      aria-label="Region"
      className="inline-flex h-9 items-center gap-0.5 rounded-lg bg-zinc-100 dark:bg-zinc-800/60 p-1"
    >
      <button
        type="button"
        role="button"
        aria-pressed={isDomestic}
        onClick={() => navigateTo(domesticPath)}
        className={cn(
          'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
          'text-zinc-600 dark:text-zinc-400',
          'hover:text-zinc-900 dark:hover:text-zinc-100',
          isDomestic && 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4A9FD4]/50'
        )}
      >
        Domestic
      </button>
      <button
        type="button"
        role="button"
        aria-pressed={isIntl}
        onClick={() => navigateTo(intlPath)}
        className={cn(
          'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
          'text-zinc-600 dark:text-zinc-400',
          'hover:text-zinc-900 dark:hover:text-zinc-100',
          isIntl && 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-50 shadow-sm',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#4A9FD4]/50'
        )}
      >
        International
      </button>
    </div>
  )
}

export { RegionToggle }
