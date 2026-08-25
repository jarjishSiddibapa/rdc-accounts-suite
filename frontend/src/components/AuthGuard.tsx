import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '@/lib/auth-context'

function ApplicationSkeleton() {
  return (
    <div
      className="app-shell-frame flex min-h-[100dvh] w-full gap-4"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <aside className="glass hidden w-64 shrink-0 rounded-[1.25rem] p-4 xl:block">
        <div className="h-11 w-44 animate-pulse rounded-xl bg-bg-soft" />
        <div className="mt-8 grid gap-2">
          {Array.from({ length: 7 }, (_, index) => (
            <div
              key={index}
              className="h-11 animate-pulse rounded-xl bg-bg-soft"
              style={{ animationDelay: `${index * 55}ms` }}
            />
          ))}
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <div className="glass h-[4.5rem] animate-pulse rounded-[1.25rem]" />
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div className="h-52 animate-pulse rounded-[1.25rem] border border-border bg-surface md:col-span-2" />
          <div className="h-64 animate-pulse rounded-[1.25rem] border border-border bg-surface" />
          <div className="h-64 animate-pulse rounded-[1.25rem] border border-border bg-surface" />
        </div>
      </div>
      <span className="sr-only">Loading workspace</span>
    </div>
  )
}

export function AuthGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <ApplicationSkeleton />
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <>{children}</>
}

export function AdminGuard({ children }: { children: ReactNode }) {
  const { user } = useAuth()

  if (user?.role !== 'admin') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export function AppGuard({ appKey, children }: { appKey: string; children: ReactNode }) {
  const { user } = useAuth()
  const allowed = user?.role === 'admin' || Boolean(user?.allowed_apps?.includes(appKey))

  if (!allowed) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
