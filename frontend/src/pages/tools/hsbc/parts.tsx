import type { ReactNode } from 'react'
import { GlassCard } from '@/components/GlassCard'
import { ApiError } from '@/lib/api'
import { cn } from '@/utils/cn'

export function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent text-sm font-bold text-accent-ink">{n}</span>
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold leading-8 text-ink">{title}</h3>
          {hint && <p className="text-sm text-ink-dim">{hint}</p>}
        </div>
      </div>
      {children}
    </GlassCard>
  )
}

export function Field({ label, children, note, className }: {
  label: string; children: ReactNode; note?: ReactNode; className?: string
}) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <span className="text-xs font-semibold text-ink-dim">{label}</span>
      {children}
      {note && <span className="text-xs leading-5 text-ink-faint">{note}</span>}
    </div>
  )
}
