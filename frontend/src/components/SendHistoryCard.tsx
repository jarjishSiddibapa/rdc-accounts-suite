import { useCallback, useEffect, useState } from 'react'
import { History } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ApiError, get } from '@/lib/api'
import { formatIndianDateTime } from '@/lib/regional'

interface LogItem {
  id: number
  status: string
  requested_by: string | null
  started_at: string | null
  to: string[]
  cc: string[]
  messages: { subject?: string }[]
  detail: { sent?: number; failed?: number; skipped?: number; broadcast?: boolean }
  error: string | null
}

const PAGE = 10

function summary(item: LogItem): string {
  const { sent, failed, skipped } = item.detail ?? {}
  if (sent !== undefined || failed !== undefined) {
    const parts = [`${sent ?? 0} sent`]
    if (failed) parts.push(`${failed} failed`)
    if (skipped) parts.push(`${skipped} skipped`)
    return parts.join(', ')
  }
  const people = [...item.to, ...item.cc]
  return people.length ? `to ${people.join(', ')}` : ''
}

/**
 * Who sent mail from this application, when, and what came of it. Every send is recorded; `refreshKey` should
 * change when a send finishes so the newest entry shows up.
 */
export function SendHistoryCard({
  baseUrl,
  title = 'Sent mail history',
  refreshKey,
}: {
  baseUrl: string
  title?: string
  refreshKey?: unknown
}) {
  const [items, setItems] = useState<LogItem[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await get<{ total: number; items: LogItem[] }>(`${baseUrl}/mail/log?limit=${PAGE}`)
      setItems(data.items)
      setTotal(data.total)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the history.')
    }
  }, [baseUrl])

  useEffect(() => { void load() }, [load, refreshKey])

  async function loadMore() {
    setLoadingMore(true)
    try {
      const data = await get<{ total: number; items: LogItem[] }>(`${baseUrl}/mail/log?limit=${PAGE}&offset=${items.length}`)
      setItems((current) => [...current, ...data.items])
      setTotal(data.total)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load more.')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <History className="h-5 w-5 text-accent" />
        <h2 className="font-display text-lg font-semibold text-ink">{title}</h2>
      </div>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {!error && items.length === 0 && <p className="text-sm text-ink-faint">Nothing has been sent from here yet.</p>}
      {items.length > 0 && (
        <ul className="flex flex-col divide-y divide-border/60">
          {items.map((item) => (
            <li key={item.id} className="flex flex-col gap-0.5 py-2.5 text-sm">
              <span className="text-ink">
                {item.started_at ? formatIndianDateTime(item.started_at) : ''} by {item.requested_by ?? 'the system'}:{' '}
                <span className={item.status === 'sent' ? 'text-emerald-600' : item.status === 'partial' ? 'text-amber-600' : 'text-red-500'}>
                  {item.status === 'sent' ? 'sent' : item.status === 'partial' ? 'partly sent' : 'not sent'}
                </span>
              </span>
              <span className="text-xs text-ink-dim">
                {item.messages[0]?.subject ? `${item.messages[0].subject}. ` : ''}{summary(item)}
              </span>
              {item.error && <span className="text-xs text-red-500">{item.error}</span>}
            </li>
          ))}
        </ul>
      )}
      {items.length < total && (
        <div className="flex justify-center">
          <Button variant="secondary" loading={loadingMore} onClick={() => void loadMore()}>Show older</Button>
        </div>
      )}
    </GlassCard>
  )
}
