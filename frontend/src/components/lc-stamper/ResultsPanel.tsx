import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, FileArchive, FileSpreadsheet, FileText, Search, Timer } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { apiUrl } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { BASE, formatDuration, statusTone, type ResultRow, type RunResult } from '@/lib/lcStamper'
import { cn } from '@/utils/cn'

const PAGE_SIZE = 50
const TONES = {
  good: 'text-emerald-600 dark:text-emerald-400',
  warn: 'text-amber-600 dark:text-amber-400',
  bad: 'text-red-600 dark:text-red-400',
  dup: 'text-ink-dim',
} as const

type Filter = 'all' | 'good' | 'warn' | 'bad' | 'dup'
const FILTERS: [Filter, string][] = [['all', 'All'], ['good', 'Verified'], ['warn', 'Flagged'], ['bad', 'Left out / not found'], ['dup', 'Duplicates']]

function Stat({ value, label, tone }: { value: number; label: string; tone?: string }) {
  return (
    <div className="subpanel p-3 text-center">
      <p className={cn('text-2xl font-bold tabular-nums text-ink', tone)}>{formatIndianNumber(value)}</p>
      <p className="text-xs text-ink-faint">{label}</p>
    </div>
  )
}

function shownDate(value: string | null): string {
  if (!value) return '—'
  const [y, m, d] = value.split('-')
  return `${d}-${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m) - 1]}-${y}`
}

function shownAmount(value: number | null): string {
  return value === null || value === undefined ? '—' : value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const READ_BY: Record<string, string> = { text: 'PDF text', online: 'Online OCR', offline: 'Offline OCR' }

export function ResultsPanel({ jobId, result }: { jobId: string; result: RunResult }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [pageNo, setPageNo] = useState(0)
  const c = result.counts
  const filterMode = result.mode === 'filter'
  const hasDates = result.rows.some((r) => r.excel_date)
  const hasAmounts = result.rows.some((r) => r.excel_amount !== null)

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return result.rows.filter((r: ResultRow) =>
      (filter === 'all' || statusTone(r.status) === filter) &&
      (!q || r.invoice.toLowerCase().includes(q) || r.document.toLowerCase().includes(q)))
  }, [result.rows, filter, query])
  const pages = Math.max(1, Math.ceil(shown.length / PAGE_SIZE))
  const current = Math.min(pageNo, pages - 1)
  const slice = shown.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)

  const read = [
    ['PDF text', c.pages_by_text ?? 0, 'bg-emerald-500'],
    ['Online OCR', c.pages_by_online_ocr ?? 0, 'bg-sky-500'],
    ['Offline OCR', c.pages_by_offline_ocr ?? 0, 'bg-amber-500'],
  ] as const
  const readTotal = read.reduce((sum, [, n]) => sum + n, 0)

  return (
    <GlassCard padding="lg" className="flex flex-col gap-5" id="lc-results">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="flex items-center gap-2 font-display text-lg font-semibold text-ink">
          {result.status === 'ok'
            ? <CheckCircle2 className="h-5 w-5 text-emerald-500" />
            : <AlertTriangle className="h-5 w-5 text-amber-500" />}
          {result.status === 'ok' ? (filterMode ? 'Pages picked out' : 'Stamped and ready') : 'No page was kept'}
        </h3>
        <p className="flex items-center gap-1.5 text-sm text-ink-dim"><Timer className="h-4 w-4" />{formatDuration(result.elapsed_s)}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat value={c.kept ?? 0} label={filterMode ? 'Pages kept' : 'Pages stamped'} tone="text-emerald-600 dark:text-emerald-400" />
        <Stat value={c.verified ?? 0} label="Verified" />
        <Stat value={c.flagged ?? 0} label="Kept, mismatch flagged" tone={c.flagged ? TONES.warn : undefined} />
        <Stat value={c.mismatches ?? 0} label="Left out (mismatch)" tone={c.mismatches ? TONES.bad : undefined} />
        <Stat value={c.duplicates ?? 0} label="Duplicates" />
        <Stat value={c.not_found ?? 0} label="Not found" tone={c.not_found ? TONES.bad : undefined} />
      </div>

      {result.warnings.map((warning) => (
        <p key={warning} className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{warning}
        </p>
      ))}

      {result.status === 'ok' && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-ink">Download</h4>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {result.outputs.map((o, i) => (
              <a key={o.name} href={apiUrl(`${BASE}/download/${jobId}/pdf/${i}`)} download
                className="subpanel flex min-w-0 items-center gap-3 px-4 py-3 text-left sm:max-w-md">
                <FileText className="h-5 w-5 shrink-0 text-red-500" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-ink">{o.name}</span>
                  <span className="text-xs text-ink-faint">{formatIndianNumber(o.pages)} page{o.pages === 1 ? '' : 's'}</span>
                </span>
                <Download className="ml-auto h-4 w-4 shrink-0 text-accent" />
              </a>
            ))}
            {result.report_name && (
              <a href={apiUrl(`${BASE}/download/${jobId}/report`)} download className="subpanel flex min-w-0 items-center gap-3 px-4 py-3 sm:max-w-md">
                <FileSpreadsheet className="h-5 w-5 shrink-0 text-emerald-600" />
                <span className="min-w-0"><span className="block truncate text-sm font-semibold text-ink">{result.report_name}</span>
                  <span className="text-xs text-ink-faint">{filterMode ? 'Found / not found, with pages' : 'Date and amount checks'}</span></span>
                <Download className="ml-auto h-4 w-4 shrink-0 text-accent" />
              </a>
            )}
            {result.outputs.length + (result.report_name ? 1 : 0) > 1 && (
              <a href={apiUrl(`${BASE}/download/${jobId}/zip`)} download className="subpanel flex items-center gap-3 px-4 py-3">
                <FileArchive className="h-5 w-5 shrink-0 text-accent" />
                <span className="text-sm font-semibold text-ink">Everything as one ZIP</span>
              </a>
            )}
          </div>
        </div>
      )}

      {readTotal > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-ink">How the pages were read</h4>
          <div className="flex h-2.5 overflow-hidden rounded-full bg-bg-soft" role="img"
            aria-label={read.map(([name, n]) => `${name}: ${n}`).join(', ')}>
            {read.map(([name, n, color]) => n > 0 && <div key={name} className={color} style={{ width: `${(n / readTotal) * 100}%` }} />)}
          </div>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-dim">
            {read.map(([name, n, color]) => (
              <span key={name} className="inline-flex items-center gap-1.5"><span className={cn('h-2 w-2 rounded-full', color)} />{name}: {formatIndianNumber(n)}</span>
            ))}
          </p>
        </div>
      )}

      {result.documents.length > 1 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold text-ink">Documents</h4>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {result.documents.map((d) => (
              <div key={d.name} className="subpanel flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0 truncate text-ink">{d.name}</span>
                <span className="shrink-0 text-xs text-ink-faint">{d.kept} of {d.pages} pages</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {result.rows.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
            <h4 className="text-sm font-semibold text-ink">
              Invoices{result.rows_total > result.rows.length ? ` (first ${formatIndianNumber(result.rows.length)} of ${formatIndianNumber(result.rows_total)}; the report has all)` : ''}
            </h4>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
                {FILTERS.map(([key, label]) => (
                  <button key={key} type="button" aria-pressed={filter === key}
                    onClick={() => { setFilter(key); setPageNo(0) }}
                    className={cn('min-h-9 rounded-full border px-3 text-xs font-medium transition',
                      filter === key ? 'border-accent bg-accent text-accent-ink' : 'border-border bg-surface text-ink-dim hover:border-accent/50')}>
                    {label}
                  </button>
                ))}
              </div>
              <label className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
                <input value={query} onChange={(e) => { setQuery(e.target.value); setPageNo(0) }} placeholder="Search invoice or file"
                  className="min-h-9 w-full rounded-lg border border-border bg-surface pl-9 pr-3 text-sm text-ink sm:w-56" />
              </label>
            </div>
          </div>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <thead className="bg-bg-soft text-xs uppercase tracking-wide text-ink-faint">
                <tr>
                  <th className="px-3 py-2">Invoice</th>
                  {filterMode && <th className="px-3 py-2">Found</th>}
                  {hasDates && <><th className="px-3 py-2">List date</th><th className="px-3 py-2">PDF date</th></>}
                  {hasAmounts && <><th className="px-3 py-2 text-right">List amount</th><th className="px-3 py-2 text-right">PDF amount</th></>}
                  <th className="px-3 py-2">Status</th><th className="px-3 py-2">Document</th>
                  <th className="px-3 py-2 text-right">Page</th><th className="px-3 py-2">Read by</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {slice.map((r, i) => (
                  <tr key={`${r.invoice}-${r.document}-${r.page}-${i}`} className="hover:bg-bg-soft/60">
                    <td className="px-3 py-2 font-medium text-ink">{r.invoice || '—'}</td>
                    {filterMode && <td className="px-3 py-2">{r.status.includes('Not Found') ? 'No' : 'Yes'}</td>}
                    {hasDates && <>
                      <td className="px-3 py-2 tabular-nums">{shownDate(r.excel_date)}</td>
                      <td className={cn('px-3 py-2 tabular-nums', r.date_match === '✗' && TONES.bad)}>{shownDate(r.pdf_date)}</td>
                    </>}
                    {hasAmounts && <>
                      <td className="px-3 py-2 text-right tabular-nums">{shownAmount(r.excel_amount)}</td>
                      <td className={cn('px-3 py-2 text-right tabular-nums', r.amount_match === '✗' && TONES.bad)}>{shownAmount(r.pdf_amount)}</td>
                    </>}
                    <td className={cn('px-3 py-2 font-medium', TONES[statusTone(r.status)])}>{r.status}</td>
                    <td className="max-w-[14rem] truncate px-3 py-2 text-ink-dim" title={r.document}>{r.document || '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-dim">{r.page ?? '—'}</td>
                    <td className="px-3 py-2 text-ink-dim">{READ_BY[r.read_by] ?? '—'}</td>
                  </tr>
                ))}
                {slice.length === 0 && <tr><td colSpan={9} className="px-3 py-6 text-center text-ink-dim">Nothing matches.</td></tr>}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="flex items-center justify-between text-sm text-ink-dim">
              <span>{formatIndianNumber(shown.length)} rows · page {current + 1} of {pages}</span>
              <div className="flex gap-2">
                <button type="button" disabled={current === 0} onClick={() => setPageNo(current - 1)}
                  className="min-h-9 rounded-lg border border-border bg-surface px-3 disabled:opacity-40">Previous</button>
                <button type="button" disabled={current >= pages - 1} onClick={() => setPageNo(current + 1)}
                  className="min-h-9 rounded-lg border border-border bg-surface px-3 disabled:opacity-40">Next</button>
              </div>
            </div>
          )}
        </div>
      )}
    </GlassCard>
  )
}
