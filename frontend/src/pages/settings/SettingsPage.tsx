import { useState, useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useBedrockStatus } from '@/hooks/useBedrockStatus'
import api from '@/lib/api'
import { Zap, Save, CheckCircle2 } from 'lucide-react'

const bedrockSchema = z.object({
  model_id: z.string().min(1, 'Model ID is required'),
  region: z.string().min(1, 'Region is required'),
  ai_trigger_mode: z.enum(['always', 'fallback', 'never']),
})

type BedrockFormData = z.infer<typeof bedrockSchema>

const aiTriggerOptions = [
  { value: 'always', label: 'Always' },
  { value: 'fallback', label: 'Fallback (rule-based first)' },
  { value: 'never', label: 'Never' },
]

const MODEL_OPTIONS = [
  { value: 'anthropic.claude-3-5-sonnet-20241022-v2:0', label: 'Claude 3.5 Sonnet v2' },
  { value: 'anthropic.claude-3-5-haiku-20241022-v1:0', label: 'Claude 3.5 Haiku' },
  { value: 'anthropic.claude-3-haiku-20240307-v1:0', label: 'Claude 3 Haiku' },
]

const REGION_OPTIONS = [
  { value: 'us-east-1', label: 'US East (N. Virginia)' },
  { value: 'us-west-2', label: 'US West (Oregon)' },
  { value: 'eu-west-1', label: 'EU (Ireland)' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)' },
]

function BedrockConfigCard() {
  const { status } = useBedrockStatus()
  const [saved, setSaved] = useState(false)

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<BedrockFormData>({
    resolver: zodResolver(bedrockSchema),
    defaultValues: {
      model_id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
      region: 'us-east-1',
      ai_trigger_mode: 'fallback',
    },
  })

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await api.get<BedrockFormData>('/api/v1/settings/bedrock')
        reset(res.data)
      } catch {
        // Use defaults
      }
    }
    void fetchConfig()
  }, [reset])

  const onSubmit = async (data: BedrockFormData) => {
    try {
      await api.patch('/api/v1/settings/bedrock', data)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      // Handle error silently for demo
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <Zap className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Bedrock Configuration</CardTitle>
              <CardDescription>AWS Bedrock model and inference settings</CardDescription>
            </div>
          </div>
          <Badge variant={status?.connected ? 'success' : 'danger'}>
            {status?.connected ? 'Connected' : 'Disconnected'}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <form id="bedrock-form" onSubmit={(e) => { void handleSubmit(onSubmit)(e) }} className="flex flex-col gap-4">
          <Select
            label="Model ID"
            value={watch('model_id')}
            onValueChange={(v) => setValue('model_id', v)}
            options={MODEL_OPTIONS}
          />
          {errors.model_id && <span className="text-xs text-red-500">{errors.model_id.message}</span>}

          <Select
            label="Region"
            value={watch('region')}
            onValueChange={(v) => setValue('region', v)}
            options={REGION_OPTIONS}
          />

          <Input
            label="Region (custom)"
            id="region-custom"
            placeholder="e.g. us-east-1"
            {...register('region')}
            error={errors.region?.message}
          />

          <Select
            label="AI Trigger Mode"
            value={watch('ai_trigger_mode')}
            onValueChange={(v) => setValue('ai_trigger_mode', v as BedrockFormData['ai_trigger_mode'])}
            options={aiTriggerOptions}
          />
        </form>
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {saved && (
          <span className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Saved
          </span>
        )}
        <Button type="submit" form="bedrock-form" loading={isSubmitting}>
          <Save className="h-4 w-4" />
          Save Changes
        </Button>
      </CardFooter>
    </Card>
  )
}

function SettingsPage() {
  return (
    <div className="max-w-2xl flex flex-col gap-6">
      <BedrockConfigCard />
    </div>
  )
}

export { SettingsPage }
