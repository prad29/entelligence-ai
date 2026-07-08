import { NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  Sparkles,
  List,
  ClipboardCheck,
  Settings,
  Menu,
  X,
  Film,
  Clapperboard,
} from 'lucide-react'
import { useState } from 'react'

interface NavItem {
  to: string
  icon: React.ReactNode
  label: string
}

const detectionGroup: NavItem[] = [
  { to: '/detection', icon: <Sparkles className="h-4 w-4" />, label: 'AI Amenity Detection' },
  { to: '/amenities', icon: <List className="h-4 w-4" />, label: 'Master Amenity List' },
  { to: '/review', icon: <ClipboardCheck className="h-4 w-4" />, label: 'Review Queue' },
]

const movieFormatGroup: NavItem[] = [
  { to: '/movie-detection', icon: <Film className="h-4 w-4" />, label: 'AI Movie Format Detection' },
  { to: '/movie-formats', icon: <List className="h-4 w-4" />, label: 'Master Movie Format List' },
  { to: '/movie-review', icon: <ClipboardCheck className="h-4 w-4" />, label: 'Review Queue' },
]

const movieTitleMatchingGroup: NavItem[] = [
  { to: '/movie-title-matching', icon: <Clapperboard className="h-4 w-4" />, label: 'AI Movie Title Matching' },
]

const systemGroup: NavItem[] = [
  { to: '/settings', icon: <Settings className="h-4 w-4" />, label: 'Settings' },
]

function SidebarNavItem({ to, icon, label, onClick }: NavItem & { onClick?: () => void }) {
  const location = useLocation()
  const isActive = location.pathname.startsWith(to)

  return (
    <NavLink
      to={to}
      onClick={onClick}
      className={cn(
        'group flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 relative',
        isActive
          ? 'bg-zinc-800 text-white dark:bg-zinc-700 before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-4 before:w-0.5 before:rounded-full before:bg-[#4A9FD4]'
          : 'text-zinc-400 dark:text-zinc-400 hover:bg-zinc-800/60 dark:hover:bg-zinc-800/40 hover:text-zinc-100'
      )}
    >
      <span className={cn(
        'shrink-0 transition-colors',
        isActive ? 'text-[#4A9FD4]' : 'text-zinc-500 group-hover:text-zinc-300'
      )}>
        {icon}
      </span>
      <span className="truncate">{label}</span>
    </NavLink>
  )
}

function NavGroup({ title, items, onItemClick }: { title: string; items: NavItem[]; onItemClick?: () => void }) {
  return (
    <div className="px-3">
      <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-widest text-zinc-600 dark:text-zinc-500">
        {title}
      </p>
      <nav className="flex flex-col gap-0.5">
        {items.map((item) => (
          <SidebarNavItem key={item.to} {...item} onClick={onItemClick} />
        ))}
      </nav>
    </div>
  )
}

function SidebarContent({ onItemClick }: { onItemClick?: () => void }) {
  return (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="px-5 py-4 border-b border-zinc-800 dark:border-zinc-800">
        <img src="/logo.png" alt="EntTelligence" className="h-8 w-auto" />
        <p className="mt-1.5 text-[11px] font-medium tracking-widest text-zinc-400 leading-tight">
          E.R.I.C.A
        </p>
        <p className="mt-0.5 text-[9px] text-zinc-600 leading-tight">
          Enttelligence Research & Insights Cinematic Assistant
        </p>
      </div>

      {/* Nav groups */}
      <div className="flex-1 overflow-y-auto py-4 flex flex-col gap-6">
        <NavGroup title="Amenities Detection" items={detectionGroup} onItemClick={onItemClick} />
        <NavGroup title="Movie Format Detection" items={movieFormatGroup} onItemClick={onItemClick} />
        <NavGroup title="AI Movie Title Matching" items={movieTitleMatchingGroup} onItemClick={onItemClick} />
        <NavGroup title="System" items={systemGroup} onItemClick={onItemClick} />
      </div>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-zinc-800 dark:border-zinc-800">
        <p className="text-[10px] text-zinc-600 dark:text-zinc-600">v1.0.0 — Phase 6</p>
      </div>
    </div>
  )
}

function AppSidebar() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex flex-col w-60 shrink-0 bg-zinc-950 dark:bg-zinc-950 border-r border-zinc-800 dark:border-zinc-800 fixed top-0 left-0 bottom-0 z-40">
        <SidebarContent />
      </aside>

      {/* Mobile toggle button */}
      <button
        className="lg:hidden fixed top-4 left-4 z-50 rounded-lg bg-zinc-950 p-2 text-zinc-400 hover:text-white transition-colors"
        onClick={() => setMobileOpen(true)}
        aria-label="Open sidebar"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Mobile drawer */}
      <aside
        className={cn(
          'lg:hidden fixed top-0 left-0 bottom-0 z-50 w-60 bg-zinc-950 border-r border-zinc-800 transition-transform duration-200',
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <button
          className="absolute top-4 right-4 rounded-lg p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
          onClick={() => setMobileOpen(false)}
          aria-label="Close sidebar"
        >
          <X className="h-4 w-4" />
        </button>
        <SidebarContent onItemClick={() => setMobileOpen(false)} />
      </aside>
    </>
  )
}

export { AppSidebar }
