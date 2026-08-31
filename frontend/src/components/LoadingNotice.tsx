import { useSyncExternalStore } from 'react'
import { Loader2 } from 'lucide-react'
import { GLOBAL_LOADING_MESSAGE, getGlobalLoadingSnapshot, subscribeGlobalLoading } from '@/lib/loading-state'
import { cn } from '@/utils/cn'

export function LoadingNotice({ className, detail }: { className?: string; detail?: string }) {
  return (
    <div
      className={cn('flex items-center justify-center gap-3 py-8 text-sm font-medium text-ink-dim', className)}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <Loader2 className="h-5 w-5 shrink-0 animate-spin text-accent motion-reduce:animate-none" />
      <span>
        {GLOBAL_LOADING_MESSAGE}
        {detail && <span className="mt-1 block text-xs font-normal text-ink-faint">{detail}</span>}
      </span>
    </div>
  )
}

export function GlobalLoadingIndicator() {
  const active = useSyncExternalStore(
    subscribeGlobalLoading,
    getGlobalLoadingSnapshot,
    getGlobalLoadingSnapshot,
  )

  if (!active) return null

  return (
    <div className="pointer-events-none fixed inset-x-3 top-3 z-[100] flex justify-center sm:inset-x-auto sm:right-5">
      <LoadingNotice className="glass max-w-full rounded-xl px-4 py-3 shadow-lg" />
    </div>
  )
}
