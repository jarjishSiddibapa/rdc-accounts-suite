import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { cn } from '@/utils/cn'

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface JobState<TResult = unknown> {
  status: JobStatus
  progress?: number // 0-100
  phase?: string
  result?: TResult
  error?: string
}

interface ProgressPanelProps<TResult = unknown> {
  jobId: string | null
  poller: (jobId: string) => Promise<JobState<TResult>>
  intervalMs?: number
  onDone?: (result: TResult | undefined) => void
  onError?: (error: string) => void
  onStatusChange?: (job: JobState<TResult>) => void
  className?: string
}

/**
 * Polls `poller(jobId)` every `intervalMs` while the job status is "queued"
 * or "running", rendering a progress bar and phase text. Stops polling as
 * soon as the job reaches "done" or "error" and fires the matching callback
 * exactly once.
 */
export function ProgressPanel<TResult = unknown>({
  jobId,
  poller,
  intervalMs = 800,
  onDone,
  onError,
  onStatusChange,
  className,
}: ProgressPanelProps<TResult>) {
  const [job, setJob] = useState<JobState<TResult> | null>(null)
  const settledRef = useRef(false)

  useEffect(() => {
    settledRef.current = false
    setJob(null)
    if (!jobId) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const tick = async () => {
      try {
        const next = await poller(jobId)
        if (cancelled) return
        setJob(next)
        onStatusChange?.(next)

        if (next.status === 'done' && !settledRef.current) {
          settledRef.current = true
          onDone?.(next.result)
          return
        }
        if (next.status === 'error' && !settledRef.current) {
          settledRef.current = true
          onError?.(next.error ?? 'Job failed')
          return
        }
        if (next.status === 'cancelled') return
        timer = setTimeout(tick, intervalMs)
      } catch (err) {
        if (cancelled) return
        if (!settledRef.current) {
          settledRef.current = true
          onError?.(err instanceof Error ? err.message : 'Job polling failed')
        }
      }
    }

    void tick()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, intervalMs])

  if (!jobId || !job) return null

  const progress = Math.min(100, Math.max(0, job.progress ?? 0))

  return (
    <div className={cn('subpanel flex flex-col gap-3 p-4', className)}>
      <div className="flex items-center gap-2 text-sm text-ink-dim">
        {job.status === 'error' || job.status === 'cancelled' ? (
          <XCircle className="h-4 w-4 text-red-500" />
        ) : job.status === 'done' ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
        )}
        <span className="flex-1">{job.phase ?? statusLabel(job.status)}</span>
        {job.status !== 'error' && job.status !== 'cancelled' && (
          <span className="font-mono text-xs font-semibold tabular-nums text-ink">
            {job.status === 'done' ? 100 : Math.round(progress)}%
          </span>
        )}
      </div>

      <div className="h-2.5 w-full overflow-hidden rounded-full border border-border/70 bg-bg-soft shadow-inner">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-300',
            job.status === 'error' || job.status === 'cancelled'
              ? 'bg-red-500'
              : 'progress-shimmer bg-accent',
          )}
          style={{ width: `${job.status === 'done' ? 100 : progress}%` }}
        />
      </div>

      {job.status === 'error' && job.error && (
        <p className="text-sm text-red-500">{job.error}</p>
      )}
    </div>
  )
}

function statusLabel(status: JobStatus): string {
  switch (status) {
    case 'queued':
      return 'Queued...'
    case 'running':
      return 'Processing...'
    case 'done':
      return 'Done'
    case 'error':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
  }
}
