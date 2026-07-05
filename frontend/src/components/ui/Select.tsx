import {
  Root,
  Trigger,
  Value,
  Icon,
  Portal,
  Content,
  Viewport,
  Item,
  ItemText,
  ItemIndicator,
  ScrollUpButton,
  ScrollDownButton,
} from '@radix-ui/react-select'
import { Check, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/utils'
import { type ReactNode } from 'react'

interface SelectOption {
  value: string
  label: string
}

interface SelectProps {
  value?: string
  onValueChange?: (value: string) => void
  options: SelectOption[]
  placeholder?: string
  label?: string
  className?: string
  triggerClassName?: string
}

function Select({ value, onValueChange, options, placeholder = 'Select…', label, className, triggerClassName }: SelectProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">{label}</span>}
      <Root value={value} onValueChange={onValueChange}>
        <Trigger
          className={cn(
            'flex h-9 w-full items-center justify-between rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100',
            'focus:outline-none focus:ring-2 focus:ring-[#4A9FD4]/30 focus:border-[#4A9FD4]',
            'data-[placeholder]:text-zinc-400 dark:data-[placeholder]:text-zinc-500',
            'transition-colors duration-150',
            triggerClassName
          )}
        >
          <Value placeholder={placeholder} />
          <Icon asChild>
            <ChevronDown className="h-4 w-4 text-zinc-400 shrink-0 ml-2" />
          </Icon>
        </Trigger>
        <Portal>
          <Content
            className={cn(
              'z-50 min-w-[8rem] overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg',
              'data-[state=open]:animate-in data-[state=closed]:animate-out',
              'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
              'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95'
            )}
            position="popper"
            sideOffset={4}
          >
            <ScrollUpButton className="flex items-center justify-center h-6 bg-white dark:bg-zinc-900">
              <ChevronUp className="h-4 w-4" />
            </ScrollUpButton>
            <Viewport className="p-1">
              {options.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </Viewport>
            <ScrollDownButton className="flex items-center justify-center h-6 bg-white dark:bg-zinc-900">
              <ChevronDown className="h-4 w-4" />
            </ScrollDownButton>
          </Content>
        </Portal>
      </Root>
    </div>
  )
}

function SelectItem({ value, children }: { value: string; children: ReactNode }) {
  return (
    <Item
      value={value}
      className={cn(
        'relative flex w-full cursor-pointer select-none items-center rounded-lg px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 outline-none',
        'focus:bg-zinc-100 dark:focus:bg-zinc-800',
        'data-[state=checked]:text-[#4A9FD4] dark:data-[state=checked]:text-[#4A9FD4]',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50'
      )}
    >
      <ItemText>{children}</ItemText>
      <ItemIndicator className="absolute right-3">
        <Check className="h-4 w-4" />
      </ItemIndicator>
    </Item>
  )
}

export { Select }
