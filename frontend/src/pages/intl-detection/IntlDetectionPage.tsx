import { useSearchParams } from 'react-router-dom'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { RegionToggle } from '@/components/ui/RegionToggle'
import { IntlSingleDetector } from './IntlSingleDetector'
import { IntlBatchUploader } from './IntlBatchUploader'

function IntlDetectionPage() {
  const [searchParams] = useSearchParams()
  const defaultValue = searchParams.get('tab') === 'batch' ? 'batch' : 'single'

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-end">
        <RegionToggle domesticPath="/detection" intlPath="/intl-detection" />
      </div>
      <Tabs
        defaultValue={defaultValue}
        tabs={[
          { value: 'single', label: 'Single' },
          { value: 'batch', label: 'Batch Upload' },
        ]}
      >
        <TabsContent value="single">
          <IntlSingleDetector />
        </TabsContent>
        <TabsContent value="batch">
          <IntlBatchUploader />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export { IntlDetectionPage }
