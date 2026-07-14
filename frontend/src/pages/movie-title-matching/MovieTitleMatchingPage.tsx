import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { MovieTitleSingleMatcher } from './MovieTitleSingleMatcher'
import { MovieTitleBatchMatcher } from './MovieTitleBatchMatcher'

const TABS = [
  { value: 'single', label: 'Single Match' },
  { value: 'batch', label: 'Batch Upload' },
]

function MovieTitleMatchingPage() {
  return (
    <div className="max-w-2xl mx-auto flex flex-col gap-6 py-6">
      <Tabs defaultValue="single" tabs={TABS}>
        <TabsContent value="single">
          <MovieTitleSingleMatcher />
        </TabsContent>
        <TabsContent value="batch">
          <MovieTitleBatchMatcher />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export { MovieTitleMatchingPage }
