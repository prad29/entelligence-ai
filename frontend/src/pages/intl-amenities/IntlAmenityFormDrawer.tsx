import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Dialog } from '@/components/ui/Dialog'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { type IntlAmenity } from '@/hooks/useIntlAmenities'

const intlAmenitySchema = z.object({
  keyword: z.string().min(1, 'Keyword is required'),
  screen_format: z.string().min(1, 'Screen format is required'),
  tier: z.enum(['P1', 'P2', 'P3', 'P4', 'P5']),
  status: z.enum(['approved', 'pending', 'rejected']),
})

type IntlAmenityFormData = z.infer<typeof intlAmenitySchema>

interface IntlAmenityFormDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  amenity?: IntlAmenity | null
  onSubmit: (data: Omit<IntlAmenity, 'id' | 'updated_at'>) => Promise<void>
}

const tierOptions = ['P1', 'P2', 'P3', 'P4', 'P5'].map((t) => ({ value: t, label: t }))
const statusOptions = [
  { value: 'approved', label: 'Approved' },
  { value: 'pending', label: 'Pending' },
  { value: 'rejected', label: 'Rejected' },
]

function IntlAmenityFormDrawer({ open, onOpenChange, amenity, onSubmit }: IntlAmenityFormDrawerProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<IntlAmenityFormData>({
    resolver: zodResolver(intlAmenitySchema),
    defaultValues: { tier: 'P3', status: 'pending' },
  })

  useEffect(() => {
    if (amenity) {
      reset({
        keyword: amenity.keyword,
        screen_format: amenity.screen_format,
        tier: amenity.tier as 'P1' | 'P2' | 'P3' | 'P4' | 'P5',
        status: amenity.status as 'approved' | 'pending' | 'rejected',
      })
    } else {
      reset({ tier: 'P3', status: 'pending', keyword: '', screen_format: '' })
    }
  }, [amenity, reset, open])

  const onFormSubmit = async (data: IntlAmenityFormData) => {
    await onSubmit({
      keyword: data.keyword,
      screen_format: data.screen_format,
      tier: data.tier,
      status: data.status,
    })
    onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={amenity ? 'Edit Mapping' : 'Add Mapping'}
      description="Define how an international amenity keyword maps to a screen format."
    >
      <form onSubmit={(e) => { void handleSubmit(onFormSubmit)(e) }} className="flex flex-col gap-4">
        <Input
          label="Keyword"
          id="intl-keyword"
          placeholder="e.g. 4DX, ScreenX, ONYX - Pathe"
          error={errors.keyword?.message}
          {...register('keyword')}
        />
        <Input
          label="Screen Format"
          id="intl-screen_format"
          placeholder="e.g. 4DX, Standard"
          error={errors.screen_format?.message}
          {...register('screen_format')}
        />
        <div className="grid grid-cols-2 gap-3">
          <Select
            label="Tier"
            value={watch('tier')}
            onValueChange={(v) => setValue('tier', v as IntlAmenityFormData['tier'])}
            options={tierOptions}
          />
          <Select
            label="Status"
            value={watch('status')}
            onValueChange={(v) => setValue('status', v as IntlAmenityFormData['status'])}
            options={statusOptions}
          />
        </div>
        <div className="flex justify-end gap-2 pt-2 border-t border-zinc-100 dark:border-zinc-800">
          <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {amenity ? 'Save Changes' : 'Add Mapping'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

export { IntlAmenityFormDrawer }
