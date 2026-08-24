import { Link } from 'react-router-dom'
import { CalendarDays, Menu } from 'lucide-react'
import { ThemeToggle } from '@/components/ThemeToggle'
import { useAuth } from '@/lib/auth-context'
import { formatIndianDate } from '@/lib/regional'
import { getUserDisplayName, getUserInitials } from '@/lib/user'

export function TopBar({ title, onMenuClick }: { title: string; onMenuClick?: () => void }) {
  const { user } = useAuth()
  const today = formatIndianDate(new Date(), {
    weekday: 'short',
    day: '2-digit',
    month: 'short',
  })

  return (
    <header className="app-topbar glass sticky z-20 flex min-h-[4.5rem] min-w-0 items-center justify-between gap-2 rounded-[1.25rem] px-3 py-3 sm:gap-3 sm:px-5">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label="Open navigation"
          aria-controls="primary-navigation"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-border bg-surface/55 text-ink-dim transition hover:border-accent/40 hover:bg-surface hover:text-accent xl:hidden"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="min-w-0">
          <p className="hidden text-[11px] font-semibold tracking-wide text-ink-faint sm:block">
            Accounts workspace
          </p>
          <h1 className="truncate font-display text-lg font-semibold tracking-[-0.025em] text-ink sm:text-xl">
            {title}
          </h1>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <div className="hidden items-center gap-2 rounded-xl border border-border bg-surface/45 px-3 py-2 text-xs font-medium text-ink-dim 2xl:flex">
          <CalendarDays className="h-3.5 w-3.5 text-accent" />
          {today}
        </div>
        <ThemeToggle />
        {user && (
          <Link
            to="/settings"
            title="Open profile settings"
            className="flex min-w-0 items-center gap-2 rounded-xl border border-border bg-surface p-1.5 text-sm shadow-[inset_0_1px_0_var(--surface-highlight)] transition duration-200 hover:border-accent/30 xl:pr-3"
          >
            <span className="icon-tile grid h-8 w-8 shrink-0 place-items-center rounded-lg text-[11px] font-bold">
              {getUserInitials(user)}
            </span>
            <span className="hidden min-w-0 leading-tight xl:block">
              <span className="block max-w-48 truncate font-medium text-ink">{getUserDisplayName(user)}</span>
              {getUserDisplayName(user) !== user.email && (
                <span className="block max-w-48 truncate text-[11px] text-ink-faint">{user.email}</span>
              )}
            </span>
          </Link>
        )}
      </div>
    </header>
  )
}
