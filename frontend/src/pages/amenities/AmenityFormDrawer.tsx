import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Dialog } from '@/components/ui/Dialog'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { type Amenity } from '@/hooks/useAmenities'

const amenitySchema = z.object({
  keyword: z.string().min(1, 'Keyword is required'),
  screen_format: z.string().min(1, 'Screen format is required'),
  tier: z.enum(['P1', 'P2', 'P3', 'P4', 'P5', 'P6']),
  circuit: z.string().optional(),
  status: z.enum(['active', 'pending', 'inactive']),
})

type AmenityFormData = z.infer<typeof amenitySchema>

interface AmenityFormDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  amenity?: Amenity | null
  onSubmit: (data: Omit<Amenity, 'id' | 'updated_at'>) => Promise<void>
}

const tierOptions = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6'].map((t) => ({ value: t, label: t }))
const statusOptions = [
  { value: 'active', label: 'Active' },
  { value: 'pending', label: 'Pending' },
  { value: 'inactive', label: 'Inactive' },
]

function AmenityFormDrawer({ open, onOpenChange, amenity, onSubmit }: AmenityFormDrawerProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<AmenityFormData>({
    resolver: zodResolver(amenitySchema),
    defaultValues: { tier: 'P3', status: 'active' },
  })

  useEffect(() => {
    if (amenity) {
      reset({
        keyword: amenity.keyword,
        screen_format: amenity.screen_format,
        tier: amenity.tier as 'P1' | 'P2' | 'P3' | 'P4' | 'P5' | 'P6',
        circuit: amenity.circuit ?? '',
        status: amenity.status as 'pending' | 'active' | 'inactive',
      })
    } else {
      reset({ tier: 'P3', status: 'active', keyword: '', screen_format: '', circuit: '' })
    }
  }, [amenity, reset, open])

  const onFormSubmit = async (data: AmenityFormData) => {
    await onSubmit({
      keyword: data.keyword,
      screen_format: data.screen_format,
      tier: data.tier,
      circuit: data.circuit || null,
      status: data.status,
    })
    onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={amenity ? 'Edit Mapping' : 'Add Mapping'}
      description="Define how an amenity keyword maps to a screen format."
    >
      <form onSubmit={(e) => { void handleSubmit(onFormSubmit)(e) }} className="flex flex-col gap-4">
        <Input
          label="Keyword"
          id="keyword"
          placeholder="e.g. IMAX, 4DX, VIP"
          error={errors.keyword?.message}
          {...register('keyword')}
        />
        <Input
          label="Screen Format"
          id="screen_format"
          placeholder="e.g. IMAX, Standard"
          error={errors.screen_format?.message}
          {...register('screen_format')}
        />
        <div className="grid grid-cols-2 gap-3">
          <Select
            label="Tier"
            value={watch('tier')}
            onValueChange={(v) => setValue('tier', v as AmenityFormData['tier'])}
            options={tierOptions}
          />
          <Select
            label="Status"
            value={watch('status')}
            onValueChange={(v) => setValue('status', v as AmenityFormData['status'])}
            options={statusOptions}
          />
        </div>
        <Input
          label="Circuit (optional)"
          id="circuit"
          placeholder="e.g. AMC, Cineplex"
          {...register('circuit')}
        />
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

export { AmenityFormDrawer }
