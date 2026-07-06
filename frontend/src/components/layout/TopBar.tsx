import { useLocation } from 'react-router-dom'
import { Sun, Moon, User, ChevronDown, LogOut, Zap } from 'lucide-react'
import { useBedrockStatus } from '@/hooks/useBedrockStatus'
import { cn } from '@/lib/utils'
import { useState } from 'react'
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu'

const pageTitles: Record<string, string> = {
  '/detection': 'AI Amenity Detection',
  '/amenities': 'Master Amenity List',
  '/circuits': 'Circuit Mappings',
  '/review': 'Review Queue',
  '/movie-detection': 'AI Movie Format Detection',
  '/movie-formats': 'Master Movie Format List',
  '/movie-review': 'Movie Format Review Queue',
  '/settings': 'Settings',
}

function BedrockStatusPill() {
  const { status, loading } = useBedrockStatus()

  if (loading) {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-400 animate-pulse">
        <Zap className="h-3 w-3" />
        Bedrock
      </span>
    )
  }

  const connected = status?.connected ?? false
  return (
    <span
      className={cn(
        'flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors',
        connected
          ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-500/20'
          : 'bg-red-500/10 text-red-600 dark:text-red-400 ring-1 ring-red-500/20'
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          connected ? 'bg-emerald-500' : 'bg-red-500'
        )}
      />
      {connected ? 'Bedrock live' : 'Bedrock offline'}
    </span>
  )
}

function DarkModeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))

  const toggle = () => {
    const next = !dark
    setDark(next)
    if (next) {
      document.documentElement.classList.add('dark')
      localStorage.setItem('theme', 'dark')
    } else {
      document.documentElement.classList.remove('dark')
      localStorage.setItem('theme', 'light')
    }
  }

  return (
    <button
      onClick={toggle}
      aria-label="Toggle dark mode"
      className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  )
}

function UserMenu() {
  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        <button
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-medium text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
          aria-label="User menu"
        >
          <div className="h-7 w-7 rounded-full bg-gradient-to-br from-[#4A9FD4] to-[#2A7FB4] flex items-center justify-center shadow-sm">
            <User className="h-3.5 w-3.5 text-white" />
          </div>
          <span className="hidden sm:block">Admin</span>
          <ChevronDown className="h-3.5 w-3.5 text-zinc-400 hidden sm:block" />
        </button>
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[160px] rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg p-1"
        >
          <DropdownMenuPrimitive.Item className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer outline-none transition-colors">
            <User className="h-4 w-4 text-zinc-400" />
            Profile
          </DropdownMenuPrimitive.Item>
          <DropdownMenuPrimitive.Separator className="my-1 h-px bg-zinc-100 dark:bg-zinc-800" />
          <DropdownMenuPrimitive.Item className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer outline-none transition-colors">
            <LogOut className="h-4 w-4" />
            Sign out
          </DropdownMenuPrimitive.Item>
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  )
}

function TopBar() {
  const location = useLocation()
  const title = Object.entries(pageTitles).find(([path]) =>
    location.pathname.startsWith(path)
  )?.[1] ?? 'Dashboard'

  return (
    <header className="fixed top-0 right-0 left-0 lg:left-60 z-30 h-16 border-b border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-md flex items-center px-4 lg:px-6">
      <div className="flex-1">
        <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-50 truncate">
          {title}
        </h2>
      </div>
      <div className="flex items-center gap-2 lg:gap-3">
        <BedrockStatusPill />
        <DarkModeToggle />
        <div className="w-px h-5 bg-zinc-200 dark:bg-zinc-700 hidden sm:block" />
        <UserMenu />
      </div>
    </header>
  )
}

export { TopBar }
