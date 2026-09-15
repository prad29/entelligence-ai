import { useState, useEffect, type KeyboardEvent } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/Card'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input } from '@/components/ui/Input'
import { useBedrockStatus } from '@/hooks/useBedrockStatus'
import api from '@/lib/api'
import { Zap, Save, CheckCircle2, ScanSearch, PlayCircle, Clock, X } from 'lucide-react'

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
  // Mistral
  { value: 'mistral.mistral-large-2407-v1:0', label: 'Mistral Large 2 (24.07)' },
  { value: 'mistral.mistral-small-2402-v1:0', label: 'Mistral Small' },
  // Amazon Nova
  { value: 'amazon.nova-pro-v1:0', label: 'Amazon Nova Pro' },
  { value: 'amazon.nova-lite-v1:0', label: 'Amazon Nova Lite' },
  { value: 'amazon.nova-micro-v1:0', label: 'Amazon Nova Micro' },
  // Meta Llama
  { value: 'meta.llama3-70b-instruct-v1:0', label: 'Meta Llama 3 70B' },
  { value: 'meta.llama3-8b-instruct-v1:0', label: 'Meta Llama 3 8B' },
  // Anthropic Claude
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
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<BedrockFormData>({
    resolver: zodResolver(bedrockSchema),
    defaultValues: {
      model_id: 'mistral.mistral-large-2407-v1:0',
      region: 'us-east-1',
      ai_trigger_mode: 'always',
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
          {errors.region && <span className="text-xs text-red-500">{errors.region.message}</span>}

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

function toISODate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

interface DqscanRecipient {
  email: string
  status: string
}

interface DqscanSettingsResponse {
  env: string
  cron_expression: string
  recipients: DqscanRecipient[]
}

const DB_OPTIONS = [
  { value: 'dev', label: 'Dev' },
  { value: 'prod', label: 'Prod' },
]

function DqscanErrorReportsCard() {
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  const [env, setEnv] = useState('dev')
  const [cronExpression, setCronExpression] = useState('0 18 * * *')
  const [recipients, setRecipients] = useState<DqscanRecipient[]>([])
  const [recipientInput, setRecipientInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const [fromDate, setFromDate] = useState(toISODate(yesterday))
  const [toDate, setToDate] = useState(toISODate(today))
  const [triggering, setTriggering] = useState(false)
  const [triggered, setTriggered] = useState(false)

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get<DqscanSettingsResponse>('/api/v1/dqscan/settings')
        setEnv(res.data.env)
        setCronExpression(res.data.cron_expression)
        setRecipients(res.data.recipients)
      } catch {
        // Use defaults
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const addRecipient = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return
    e.preventDefault()
    const email = recipientInput.trim()
    if (email && !recipients.some((r) => r.email === email)) {
      setRecipients([...recipients, { email, status: 'unsaved' }])
    }
    setRecipientInput('')
  }

  const removeRecipient = (email: string) => {
    setRecipients(recipients.filter((r) => r.email !== email))
  }

  const onSave = async () => {
    setSaving(true)
    try {
      const res = await api.put<DqscanSettingsResponse>('/api/v1/dqscan/settings', {
        env,
        cron_expression: cronExpression,
        recipients: recipients.map((r) => r.email),
      })
      setRecipients(res.data.recipients)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      // Handle error silently for demo
    } finally {
      setSaving(false)
    }
  }

  const onTrigger = async () => {
    setTriggering(true)
    try {
      await api.post('/api/v1/dqscan/trigger', { from_date: fromDate, to_date: toDate })
      setTriggered(true)
      setTimeout(() => setTriggered(false), 4000)
    } catch {
      // Handle error silently for demo
    } finally {
      setTriggering(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-lg bg-amber-600/10 dark:bg-amber-600/20 flex items-center justify-center">
            <Clock className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <CardTitle>Error Reports (Movie Shows)</CardTitle>
            <CardDescription>Cron schedule, recipients, and manual trigger for the movies_shows data-quality scan</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4">
          <div className="flex gap-4">
            <Select
              label="Database"
              value={env}
              onValueChange={setEnv}
              options={DB_OPTIONS}
            />
            <Input
              label="Cron Trigger Time (cron expression, UTC)"
              value={cronExpression}
              onChange={(e) => setCronExpression(e.target.value)}
              placeholder="0 18 * * *"
            />
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Standard 5-field cron format (minute hour day month weekday), UTC. Default is 6 PM daily
            (<code>0 18 * * *</code>). Also used as the database target for the manual scan below.
          </p>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">Recipient List</label>
            <div className="flex flex-wrap gap-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-2 min-h-[2.5rem]">
              {recipients.map((r) => (
                <span
                  key={r.email}
                  className="inline-flex items-center gap-1 rounded-md bg-zinc-100 dark:bg-zinc-800 px-2 py-1 text-xs text-zinc-700 dark:text-zinc-200"
                >
                  {r.email}
                  {r.status === 'pending' && (
                    <span className="text-amber-600 dark:text-amber-400">(pending confirmation)</span>
                  )}
                  <button
                    type="button"
                    onClick={() => removeRecipient(r.email)}
                    className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              <input
                type="email"
                value={recipientInput}
                onChange={(e) => setRecipientInput(e.target.value)}
                onKeyDown={addRecipient}
                placeholder="Add email, press Enter"
                className="flex-1 min-w-[10rem] bg-transparent text-sm outline-none placeholder:text-zinc-400 dark:placeholder:text-zinc-500 text-zinc-900 dark:text-zinc-100"
              />
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Each address must click a one-time AWS confirmation email before they start receiving reports.
            </p>
          </div>

          <div className="flex items-center justify-end">
            {saved && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 mr-2">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Saved
              </span>
            )}
            <Button type="button" onClick={() => { void onSave() }} loading={saving || loading}>
              <Save className="h-4 w-4" />
              Save
            </Button>
          </div>

          <div className="border-t border-zinc-200 dark:border-zinc-700 pt-4 flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <ScanSearch className="h-4 w-4 text-blue-600 dark:text-blue-400" />
              <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Manual Scan</h3>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Run the scan right now, for a specific window.
            </p>
            <div className="flex gap-4">
              <Input
                type="date"
                label="From"
                value={fromDate}
                max={toDate}
                onChange={(e) => setFromDate(e.target.value)}
              />
              <Input
                type="date"
                label="To"
                value={toDate}
                min={fromDate}
                onChange={(e) => setToDate(e.target.value)}
              />
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Defaults to the last 24 hours. Uses the database target and recipients set above.
            </p>
          </div>
        </div>
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {triggered && (
          <span className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Scan triggered
          </span>
        )}
        <Button type="button" onClick={() => { void onTrigger() }} loading={triggering}>
          <PlayCircle className="h-4 w-4" />
          Trigger DQ Scan
        </Button>
      </CardFooter>
    </Card>
  )
}

function SettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <BedrockConfigCard />
      <DqscanErrorReportsCard />
    </div>
  )
}

export { SettingsPage }
