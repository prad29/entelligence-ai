import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { SingleDetector } from './SingleDetector'
import { BatchUploader } from './BatchUploader'

function DetectionPage() {
  return (
    <Tabs
      defaultValue="single"
      tabs={[
        { value: 'single', label: 'Single' },
        { value: 'batch', label: 'Batch Upload' },
      ]}
    >
      <TabsContent value="single">
        <SingleDetector />
      </TabsContent>
      <TabsContent value="batch">
        <BatchUploader />
      </TabsContent>
    </Tabs>
  )
}

export { DetectionPage }
