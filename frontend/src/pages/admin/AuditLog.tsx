import { useEffect, useState } from 'react'
import { ClipboardList } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Pagination } from '@/components/Pagination'
import { SearchBox } from '@/components/SearchBox'
import { ApiError, get } from '@/lib/api'
import { formatIndianDateTime } from '@/lib/regional'

interface AuditRow {
  id: number
  timestamp: string
  actor_email: string | null
  action: string
  status_code: number | null
  ip_address: string | null
  details: string | null
}

function statusColor(code: number | null): string {
  if (code == null) return 'text-ink-faint'
  if (code >= 500) return 'text-red-500'
  if (code >= 400) return 'text-amber-500'
  return 'text-emerald-600'
}

export default function AuditLog() {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [actionFilter, setActionFilter] = useState('')
  const [actorFilter, setActorFilter] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
    })
    if (actionFilter.trim()) params.set('action_contains', actionFilter.trim())
    if (actorFilter.trim()) params.set('actor_contains', actorFilter.trim())
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)

    get<{ total: number; items: AuditRow[] }>(`/admin/audit-log?${params.toString()}`)
      .then((data) => {
        if (cancelled) return
        setRows(data.items)
        setTotal(data.total)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'Failed to load the audit log.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [page, pageSize, actionFilter, actorFilter, startDate, endDate])

  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  return (
    <AppShell title="Audit log">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex items-start gap-3">
            <div className="icon-tile grid h-11 w-11 shrink-0 place-items-center rounded-2xl">
              <ClipboardList className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Accountability</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Every action, who did it, when
              </h2>
              <p className="mt-1 text-sm text-ink-dim">
                Every API call across every tool is recorded here — actor, action, result, IP, and
                timing. This same trail is also mirrored to <code>logs/audit.log</code> on disk,
                independent of the database.
              </p>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="lg">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <SearchBox
              value={actorFilter}
              onChange={(value) => {
                setActorFilter(value)
                setPage(1)
              }}
              placeholder="Search by actor email or name"
              aria-label="Filter audit log by actor email or name"
            />
            <SearchBox
              value={actionFilter}
              onChange={(value) => {
                setActionFilter(value)
                setPage(1)
              }}
              placeholder="Filter by action (e.g. /api/admin/users)"
              aria-label="Filter audit log by action"
            />
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="sr-only">From date</span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => {
                  setStartDate(e.target.value)
                  setPage(1)
                }}
                aria-label="From date"
                className="field-control"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="sr-only">To date</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => {
                  setEndDate(e.target.value)
                  setPage(1)
                }}
                aria-label="To date"
                className="field-control"
              />
            </label>
          </div>

          {error && (
            <p className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {error}
            </p>
          )}

          <div className="mt-4 overflow-x-auto rounded-2xl border border-border">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-bg-soft/70 text-xs font-semibold tracking-wide text-ink-faint uppercase">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Actor</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {loading ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-6 text-center text-ink-faint">
                      Loading…
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-6 text-center text-ink-faint">
                      No matching entries.
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <tr key={row.id}>
                      <td className="px-4 py-3 whitespace-nowrap text-ink-dim">
                        {formatIndianDateTime(row.timestamp)}
                      </td>
                      <td className="px-4 py-3 text-ink">{row.actor_email ?? '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs text-ink">{row.action}</td>
                      <td className={`px-4 py-3 font-semibold ${statusColor(row.status_code)}`}>
                        {row.status_code ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-ink-faint">{row.ip_address ?? '—'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <Pagination
            page={page}
            pageCount={pageCount}
            pageSize={pageSize}
            totalItems={total}
            itemLabel={`audit ${total === 1 ? 'entry' : 'entries'}`}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size)
              setPage(1)
            }}
            pageSizeOptions={[25, 50, 100, 250]}
            className="mt-4"
          />
        </GlassCard>
      </div>
    </AppShell>
  )
}
