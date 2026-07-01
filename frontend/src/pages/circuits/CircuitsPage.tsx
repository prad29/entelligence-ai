import { useState } from 'react'
import { Tabs, TabsContent } from '@/components/ui/Tabs'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { Input } from '@/components/ui/Input'
import { Plus, Pencil, Trash2 } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

interface CircuitOverride {
  id: string
  circuit_name: string
  amenity_string_pattern: string
  mapped_format: string
  priority: number
}

interface CircuitAlias {
  id: string
  alias: string
  canonical: string
}

const SAMPLE_OVERRIDES: CircuitOverride[] = [
  { id: '1', circuit_name: 'AMC', amenity_string_pattern: 'Prime*', mapped_format: 'AMC Prime', priority: 1 },
  { id: '2', circuit_name: 'Cineplex', amenity_string_pattern: 'UltraAVX*', mapped_format: 'UltraAVX', priority: 1 },
  { id: '3', circuit_name: 'Regal', amenity_string_pattern: 'RPX*', mapped_format: 'RPX', priority: 1 },
]

const SAMPLE_ALIASES: CircuitAlias[] = [
  { id: '1', alias: 'AMC Theatres', canonical: 'AMC' },
  { id: '2', alias: 'Cineplex Entertainment', canonical: 'Cineplex' },
  { id: '3', alias: 'Regal Cinemas', canonical: 'Regal' },
]

const overrideSchema = z.object({
  circuit_name: z.string().min(1),
  amenity_string_pattern: z.string().min(1),
  mapped_format: z.string().min(1),
  priority: z.number().int().min(1).max(10),
})

const aliasSchema = z.object({
  alias: z.string().min(1),
  canonical: z.string().min(1),
})

type OverrideForm = z.infer<typeof overrideSchema>
type AliasForm = z.infer<typeof aliasSchema>

function OverridesTab() {
  const [overrides, setOverrides] = useState<CircuitOverride[]>(SAMPLE_OVERRIDES)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<CircuitOverride | null>(null)

  const { register, handleSubmit, reset, formState: { errors, isSubmitting } } = useForm<OverrideForm>({
    resolver: zodResolver(overrideSchema),
    defaultValues: { priority: 1 },
  })

  const openAdd = () => {
    setEditTarget(null)
    reset({ priority: 1, circuit_name: '', amenity_string_pattern: '', mapped_format: '' })
    setDialogOpen(true)
  }

  const openEdit = (row: CircuitOverride) => {
    setEditTarget(row)
    reset({ ...row })
    setDialogOpen(true)
  }

  const onSubmit = async (data: OverrideForm) => {
    if (editTarget) {
      setOverrides((prev) => prev.map((o) => (o.id === editTarget.id ? { ...editTarget, ...data } : o)))
    } else {
      setOverrides((prev) => [...prev, { id: String(Date.now()), ...data }])
    }
    setDialogOpen(false)
  }

  const columns: Column<CircuitOverride>[] = [
    { key: 'circuit_name', header: 'Circuit', sortable: true },
    { key: 'amenity_string_pattern', header: 'Pattern', cell: (r) => (
      <code className="font-mono text-xs bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded">{r.amenity_string_pattern}</code>
    )},
    { key: 'mapped_format', header: 'Mapped Format', sortable: true },
    { key: 'priority', header: 'Priority', cell: (r) => (
      <span className="font-mono text-xs text-zinc-500">{r.priority}</span>
    )},
    { key: 'actions', header: '', cell: (row) => (
      <div className="flex items-center gap-1 justify-end">
        <button onClick={() => openEdit(row)} className="rounded-md p-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button onClick={() => setOverrides((prev) => prev.filter((o) => o.id !== row.id))} className="rounded-md p-1.5 text-zinc-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    )},
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button size="sm" onClick={openAdd}><Plus className="h-3.5 w-3.5" /> Add Override</Button>
      </div>
      <DataTable columns={columns} data={overrides} keyExtractor={(r) => r.id} emptyMessage="No circuit overrides." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen} title={editTarget ? 'Edit Override' : 'Add Override'}>
        <form onSubmit={(e) => { void handleSubmit(onSubmit)(e) }} className="flex flex-col gap-3">
          <Input label="Circuit Name" error={errors.circuit_name?.message} {...register('circuit_name')} />
          <Input label="Pattern" placeholder="e.g. Prime*" error={errors.amenity_string_pattern?.message} {...register('amenity_string_pattern')} />
          <Input label="Mapped Format" error={errors.mapped_format?.message} {...register('mapped_format')} />
          <Input label="Priority" type="number" error={errors.priority?.message} {...register('priority', { valueAsNumber: true })} />
          <div className="flex justify-end gap-2 pt-2 border-t border-zinc-100 dark:border-zinc-800">
            <Button type="button" variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button type="submit" loading={isSubmitting}>{editTarget ? 'Save' : 'Add'}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  )
}

function AliasesTab() {
  const [aliases, setAliases] = useState<CircuitAlias[]>(SAMPLE_ALIASES)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<CircuitAlias | null>(null)

  const { register, handleSubmit, reset, formState: { errors, isSubmitting } } = useForm<AliasForm>({
    resolver: zodResolver(aliasSchema),
  })

  const openAdd = () => {
    setEditTarget(null)
    reset({ alias: '', canonical: '' })
    setDialogOpen(true)
  }

  const openEdit = (row: CircuitAlias) => {
    setEditTarget(row)
    reset({ ...row })
    setDialogOpen(true)
  }

  const onSubmit = async (data: AliasForm) => {
    if (editTarget) {
      setAliases((prev) => prev.map((a) => (a.id === editTarget.id ? { ...editTarget, ...data } : a)))
    } else {
      setAliases((prev) => [...prev, { id: String(Date.now()), ...data }])
    }
    setDialogOpen(false)
  }

  const columns: Column<CircuitAlias>[] = [
    { key: 'alias', header: 'Alias', sortable: true },
    { key: 'canonical', header: 'Canonical Name', sortable: true, cell: (r) => (
      <span className="font-medium text-violet-600 dark:text-violet-400">{r.canonical}</span>
    )},
    { key: 'actions', header: '', cell: (row) => (
      <div className="flex items-center gap-1 justify-end">
        <button onClick={() => openEdit(row)} className="rounded-md p-1.5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button onClick={() => setAliases((prev) => prev.filter((a) => a.id !== row.id))} className="rounded-md p-1.5 text-zinc-300 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    )},
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button size="sm" onClick={openAdd}><Plus className="h-3.5 w-3.5" /> Add Alias</Button>
      </div>
      <DataTable columns={columns} data={aliases} keyExtractor={(r) => r.id} emptyMessage="No circuit aliases." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen} title={editTarget ? 'Edit Alias' : 'Add Alias'}>
        <form onSubmit={(e) => { void handleSubmit(onSubmit)(e) }} className="flex flex-col gap-3">
          <Input label="Alias" placeholder="e.g. AMC Theatres" error={errors.alias?.message} {...register('alias')} />
          <Input label="Canonical Name" placeholder="e.g. AMC" error={errors.canonical?.message} {...register('canonical')} />
          <div className="flex justify-end gap-2 pt-2 border-t border-zinc-100 dark:border-zinc-800">
            <Button type="button" variant="secondary" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button type="submit" loading={isSubmitting}>{editTarget ? 'Save' : 'Add'}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  )
}

function CircuitsPage() {
  return (
    <Tabs
      defaultValue="overrides"
      tabs={[
        { value: 'overrides', label: 'Circuit Overrides' },
        { value: 'aliases', label: 'Circuit Aliases' },
      ]}
    >
      <TabsContent value="overrides">
        <OverridesTab />
      </TabsContent>
      <TabsContent value="aliases">
        <AliasesTab />
      </TabsContent>
    </Tabs>
  )
}

export { CircuitsPage }
