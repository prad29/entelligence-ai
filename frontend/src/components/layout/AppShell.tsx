import { type ReactNode } from 'react'
import { AppSidebar } from './AppSidebar'
import { TopBar } from './TopBar'

interface AppShellProps {
  children: ReactNode
}

function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-screen w-full bg-zinc-50 dark:bg-zinc-950">
      <AppSidebar />
      <div className="flex-1 flex flex-col lg:ml-60 min-w-0">
        <TopBar />
        <main className="flex-1 pt-16 w-full">
          <div className="p-4 lg:p-6 w-full">{children}</div>
        </main>
      </div>
    </div>
  )
}

export { AppShell }
