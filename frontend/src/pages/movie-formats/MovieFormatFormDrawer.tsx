import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Dialog } from '@/components/ui/Dialog'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { type MovieFormat } from '@/hooks/useMovieFormats'

const movieFormatSchema = z.object({
  keyword: z.string().min(1, 'Keyword is required'),
  format: z.enum(['70MM', '35MM', '3D', '2D']),
  tier: z.enum(['P1', 'P2', 'P3', 'P4']),
  status: z.enum(['approved', 'pending']),
})

type MovieFormatFormData = z.infer<typeof movieFormatSchema>

interface MovieFormatFormDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  format?: MovieFormat | null
  onSubmit: (data: Omit<MovieFormat, 'id' | 'updated_at'>) => Promise<void>
}

const tierOptions = ['P1', 'P2', 'P3', 'P4'].map((t) => ({ value: t, label: t }))
const formatOptions = ['70MM', '35MM', '3D', '2D'].map((f) => ({ value: f, label: f }))
const statusOptions = [
  { value: 'approved', label: 'Approved' },
  { value: 'pending', label: 'Pending' },
]

function MovieFormatFormDrawer({ open, onOpenChange, format, onSubmit }: MovieFormatFormDrawerProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<MovieFormatFormData>({
    resolver: zodResolver(movieFormatSchema),
    defaultValues: { tier: 'P4', status: 'approved', format: '2D' },
  })

  useEffect(() => {
    if (format) {
      reset({
        keyword: format.keyword,
        format: format.format as MovieFormatFormData['format'],
        tier: format.tier as MovieFormatFormData['tier'],
        status: format.status as MovieFormatFormData['status'],
      })
    } else {
      reset({ tier: 'P4', status: 'approved', keyword: '', format: '2D' })
    }
  }, [format, reset, open])

  const onFormSubmit = async (data: MovieFormatFormData) => {
    await onSubmit({
      keyword: data.keyword,
      format: data.format,
      tier: data.tier,
      status: data.status,
    })
    onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={format ? 'Edit Format Mapping' : 'Add Format Mapping'}
      description="Define how a keyword maps to a movie format."
    >
      <form onSubmit={(e) => { void handleSubmit(onFormSubmit)(e) }} className="flex flex-col gap-4">
        <Input
          label="Keyword"
          id="keyword"
          placeholder="e.g. 70mm, IMAX, PLF"
          error={errors.keyword?.message}
          {...register('keyword')}
        />
        <div className="grid grid-cols-2 gap-3">
          <Select
            label="Movie Format"
            value={watch('format')}
            onValueChange={(v) => setValue('format', v as MovieFormatFormData['format'])}
            options={formatOptions}
          />
          <Select
            label="Tier"
            value={watch('tier')}
            onValueChange={(v) => setValue('tier', v as MovieFormatFormData['tier'])}
            options={tierOptions}
          />
        </div>
        <Select
          label="Status"
          value={watch('status')}
          onValueChange={(v) => setValue('status', v as MovieFormatFormData['status'])}
          options={statusOptions}
        />
        <div className="flex justify-end gap-2 pt-2 border-t border-zinc-100 dark:border-zinc-800">
          <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {format ? 'Save Changes' : 'Add Mapping'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

export { MovieFormatFormDrawer }
