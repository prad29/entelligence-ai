import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { MovieSingleDetector } from './MovieSingleDetector'
import { MovieBatchUploader } from './MovieBatchUploader'

function MovieDetectionPage() {
  return (
    <Tabs
      defaultValue="single"
      tabs={[
        { value: 'single', label: 'Single' },
        { value: 'batch', label: 'Batch Upload' },
      ]}
    >
      <TabsContent value="single">
        <MovieSingleDetector />
      </TabsContent>
      <TabsContent value="batch">
        <MovieBatchUploader />
      </TabsContent>
    </Tabs>
  )
}

export { MovieDetectionPage }
