import { useState } from 'react'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { MovieTitleSingleMatcher } from './MovieTitleSingleMatcher'
import { MovieTitleBatchMatcher } from './MovieTitleBatchMatcher'

export type Market = 'domestic' | 'international'

const TABS = [
  { value: 'single', label: 'Single Match' },
  { value: 'batch', label: 'Batch Upload' },
]

function MovieTitleMatchingPage() {
  const [market, setMarket] = useState<Market>('domestic')

  return (
    <div className="max-w-2xl mx-auto flex flex-col gap-6 py-6">
      <Tabs defaultValue="single" tabs={TABS}>
        <TabsContent value="single">
          <MovieTitleSingleMatcher market={market} onMarketChange={setMarket} />
        </TabsContent>
        <TabsContent value="batch">
          <MovieTitleBatchMatcher market={market} onMarketChange={setMarket} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export { MovieTitleMatchingPage }
