import { useState } from 'react'
import { Globe2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { MovieTitleSingleMatcher } from './MovieTitleSingleMatcher'
import { MovieTitleBatchMatcher } from './MovieTitleBatchMatcher'

export type Market = 'domestic' | 'international'

const TABS = [
  { value: 'single', label: 'Single Match' },
  { value: 'batch', label: 'Batch Upload' },
]

function MarketToggle({ market, onChange }: { market: Market; onChange: (m: Market) => void }) {
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
        {isIntl ? 'Matching against Movie Master International' : 'Matching against Movie Master (domestic)'}
      </span>
    </button>
  )
}

function MovieTitleMatchingPage() {
  const [market, setMarket] = useState<Market>('domestic')

  return (
    <div className="max-w-2xl mx-auto flex flex-col gap-4 py-6">
      <MarketToggle market={market} onChange={setMarket} />
      <Tabs defaultValue="single" tabs={TABS}>
        <TabsContent value="single">
          <MovieTitleSingleMatcher market={market} />
        </TabsContent>
        <TabsContent value="batch">
          <MovieTitleBatchMatcher market={market} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export { MovieTitleMatchingPage }
