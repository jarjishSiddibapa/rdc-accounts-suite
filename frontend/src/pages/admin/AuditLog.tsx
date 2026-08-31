import { useEffect, useState } from 'react'
import { ClipboardList, Filter, RotateCcw } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Pagination } from '@/components/Pagination'
import { SearchBox } from '@/components/SearchBox'
import { Button } from '@/components/Button'
import { LoadingNotice } from '@/components/LoadingNotice'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { ApiError, get } from '@/lib/api'
import { formatIndianDateTime, formatIndianNumber } from '@/lib/regional'

interface AuditRow {
  id: number
  timestamp: string
  actor_email: string | null
  action: string
  status_code: number | null
  ip_address: string | null
  details: string | null
}

type HttpMethodFilter = '' | 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
type StatusGroupFilter = '' | 'success' | 'client_error' | 'server_error' | 'no_status'

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
  const [searchFilter, setSearchFilter] = useState('')
  const [methodFilter, setMethodFilter] = useState<HttpMethodFilter>('')
  const [statusFilter, setStatusFilter] = useState<StatusGroupFilter>('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const debouncedSearch = useDebouncedValue(searchFilter)
  const debouncedAction = useDebouncedValue(actionFilter)
  const debouncedActor = useDebouncedValue(actorFilter)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
    })
    if (debouncedSearch.trim()) params.set('search', debouncedSearch.trim())
    if (debouncedAction.trim()) params.set('action_contains', debouncedAction.trim())
    if (debouncedActor.trim()) params.set('actor_contains', debouncedActor.trim())
    if (methodFilter) params.set('method', methodFilter)
    if (statusFilter) params.set('status_group', statusFilter)
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
  }, [page, pageSize, debouncedAction, debouncedActor, debouncedSearch, endDate, methodFilter, startDate, statusFilter])

  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const hasFilters = Boolean(
    searchFilter || actionFilter || actorFilter || methodFilter || statusFilter || startDate || endDate,
  )

  function clearFilters() {
    setSearchFilter('')
    setActionFilter('')
    setActorFilter('')
    setMethodFilter('')
    setStatusFilter('')
    setStartDate('')
    setEndDate('')
    setPage(1)
  }

  return (
    <AppShell title="Audit log">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex items-start gap-3">
            <div className="icon-tile grid h-11 w-11 shrink-0 place-items-center rounded-2xl">
              <ClipboardList className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-accent">Accountability</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Every action, who did it, when
              </h2>
              <p className="mt-1 text-sm text-ink-dim">
                Every API call across every tool is recorded here, including actor, action, result, IP, and
                timing. This same trail is also mirrored to <code>logs/audit.log</code> on disk,
                independent of the database.
              </p>
            </div>
          </div>
        </GlassCard>

        <GlassCard padding="lg">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-dim">
              <Filter className="h-4 w-4 text-accent" />
              Search and filter activity
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs font-medium text-ink-faint">{formatIndianNumber(total)} found</span>
              {hasFilters && (
                <Button
                  type="button"
                  variant="secondary"
                  icon={<RotateCcw className="h-4 w-4" />}
                  onClick={clearFilters}
                >
                  Clear filters
                </Button>
              )}
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <SearchBox
              value={searchFilter}
              onChange={(value) => {
                setSearchFilter(value)
                setPage(1)
              }}
              placeholder="Search user, API, IP, or details"
              aria-label="Search all audit log fields"
              className="md:col-span-2"
            />
            <SearchBox
              value={actorFilter}
              onChange={(value) => {
                setActorFilter(value)
                setPage(1)
              }}
              placeholder="User email or name"
              aria-label="Filter audit log by actor email or name"
            />
            <SearchBox
              value={actionFilter}
              onChange={(value) => {
                setActionFilter(value)
                setPage(1)
              }}
              placeholder="API route or action"
              aria-label="Filter audit log by action"
            />
            <label className="flex flex-col gap-1 text-xs font-medium text-ink-faint">
              HTTP method
              <select
                value={methodFilter}
                onChange={(event) => {
                  setMethodFilter(event.target.value as HttpMethodFilter)
                  setPage(1)
                }}
                className="field-control text-sm text-ink"
              >
                <option value="">All methods</option>
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
                <option value="DELETE">DELETE</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-ink-faint">
              Outcome
              <select
                value={statusFilter}
                onChange={(event) => {
                  setStatusFilter(event.target.value as StatusGroupFilter)
                  setPage(1)
                }}
                className="field-control text-sm text-ink"
              >
                <option value="">All outcomes</option>
                <option value="success">Successful (2xx-3xx)</option>
                <option value="client_error">Client errors (4xx)</option>
                <option value="server_error">Server errors (5xx)</option>
                <option value="no_status">No HTTP status</option>
              </select>
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="text-xs font-medium text-ink-faint">From date</span>
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
              <span className="text-xs font-medium text-ink-faint">To date</span>
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
                      <LoadingNotice className="py-1" />
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
                      <td className="px-4 py-3 text-ink">{row.actor_email ?? 'Not available'}</td>
                      <td className="px-4 py-3 font-mono text-xs text-ink">{row.action}</td>
                      <td className={`px-4 py-3 font-semibold ${statusColor(row.status_code)}`}>
                        {row.status_code ?? 'Not available'}
                      </td>
                      <td className="px-4 py-3 text-ink-faint">{row.ip_address ?? 'Not available'}</td>
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
