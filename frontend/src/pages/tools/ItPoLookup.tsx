import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Clock3, HelpCircle, Search, Upload, XCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/Button'
import { GlassCard } from '@/components/GlassCard'
import { LoadingNotice } from '@/components/LoadingNotice'
import { Pagination } from '@/components/Pagination'
import { ApiError, get, post, postForm } from '@/lib/api'
import { PUBLIC_ISSUE_MESSAGE } from '@/lib/error-visibility'
import { formatIndianDate, formatIndianDateTime, formatIndianNumber } from '@/lib/regional'

const BASE = '/tools/it-po-lookup'

interface StatusResponse {
  role: 'it' | 'accounts' | 'both'
  can_upload: boolean
}

interface SearchResultRow {
  po_number: string
  outcome: 'found' | 'payment_in_process' | 'payment_not_processed' | 'po_not_found'
  document_number?: string | null
  utr_number?: string | null
  transaction_date?: string | null
  value_date?: string | null
  transaction_description?: string | null
  transaction_amount?: number | null
}

interface UploadRow {
  id: number
  filename: string
  account_number: string | null
  from_date: string | null
  to_date: string | null
  row_count: number
  inserted_count: number
  duplicate_count: number
  uploaded_at: string | null
}

interface Page<T> { total: number; items: T[] }

const OUTCOME_META: Record<SearchResultRow['outcome'], { label: string; className: string; icon: typeof CheckCircle2 }> = {
  found: { label: 'Found', className: 'bg-emerald-500/10 text-emerald-600', icon: CheckCircle2 },
  payment_in_process: { label: 'Payment in process', className: 'bg-amber-500/10 text-amber-600', icon: Clock3 },
  payment_not_processed: { label: 'Payment not processed', className: 'bg-red-500/10 text-red-500', icon: XCircle },
  po_not_found: { label: 'PO not found in ERP', className: 'bg-ink-faint/15 text-ink-dim', icon: HelpCircle },
}

function OutcomeBadge({ outcome }: { outcome: SearchResultRow['outcome'] }) {
  const meta = OUTCOME_META[outcome]
  const Icon = meta.icon
  return <span className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-semibold ${meta.className}`}><Icon className="h-3.5 w-3.5" />{meta.label}</span>
}

export default function ItPoLookup() {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [poText, setPoText] = useState('')
  const [results, setResults] = useState<SearchResultRow[] | null>(null)
  const [uploads, setUploads] = useState<UploadRow[]>([])
  const [uploadTotal, setUploadTotal] = useState(0)
  const [uploadPage, setUploadPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const loadUploads = useCallback(async () => {
    const page = await get<Page<UploadRow>>(`${BASE}/uploads?limit=10&offset=${(uploadPage - 1) * 10}`)
    setUploads(page.items); setUploadTotal(page.total)
  }, [uploadPage])

  useEffect(() => {
    void (async () => {
      setLoading(true)
      try {
        const next = await get<StatusResponse>(`${BASE}/status`)
        setStatus(next)
        if (next.can_upload) await loadUploads()
      } catch (error) {
        setMessage({ ok: false, text: error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE })
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  useEffect(() => { if (!loading && status?.can_upload) void loadUploads() }, [uploadPage])

  async function runSearch() {
    if (!poText.trim()) return
    setBusy('search'); setMessage(null); setResults(null)
    try {
      const response = await post<{ items: SearchResultRow[] }>(`${BASE}/search`, { po_numbers: poText })
      setResults(response.items)
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE })
    } finally {
      setBusy(null)
    }
  }

  async function uploadStatement(file: File) {
    setBusy('upload'); setMessage(null)
    try {
      const form = new FormData(); form.append('file', file)
      const result = await postForm<{ filename: string; inserted_count: number; duplicate_count: number; row_count: number; account_number: string | null }>(`${BASE}/upload`, form)
      setMessage({ ok: true, text: `${result.filename} processed: ${formatIndianNumber(result.inserted_count)} new transaction(s) added, ${formatIndianNumber(result.duplicate_count)} already present.` })
      await loadUploads()
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? error.message : PUBLIC_ISSUE_MESSAGE })
    } finally {
      setBusy(null)
    }
  }

  if (loading || !status) return <AppShell title="IT PO Lookup"><LoadingNotice className="glass rounded-2xl" /></AppShell>

  const isRestricted = status.role === 'it'

  return (
    <AppShell title="IT PO Lookup">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="overflow-hidden">
          <div className="flex items-start gap-4">
            <span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl"><Search className="h-5 w-5" /></span>
            <div>
              <p className="text-sm font-semibold text-accent">ERP + bank reconciliation</p>
              <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-ink">Find a PO's payment UTR</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-dim">
                Paste one or more PO numbers to see whether each has been paid, and if so, the exact bank transaction it was paid in.
                {isRestricted && ' As an IT user, you see the UTR and transaction details only - not the ERP document number.'}
              </p>
            </div>
          </div>
        </GlassCard>

        {message && <p role="alert" className={`rounded-xl border px-4 py-3 text-sm ${message.ok ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600' : 'border-red-500/30 bg-red-500/10 text-red-500'}`}>{message.text}</p>}

        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div>
            <h2 className="font-display text-xl font-semibold text-ink">Search POs</h2>
            <p className="mt-1 text-sm text-ink-dim">Comma-separated PO numbers.</p>
          </div>
          <textarea
            className="field-control min-h-32 font-mono text-sm"
            placeholder="e.g. 654377, 654423, 652440"
            value={poText}
            onChange={(event) => setPoText(event.target.value)}
          />
          <div className="flex justify-end">
            <Button icon={<Search className="h-4 w-4" />} loading={busy === 'search'} disabled={!poText.trim() || Boolean(busy)} onClick={() => void runSearch()}>Search</Button>
          </div>

          {results && (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[60rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-ink-faint">
                    <th className="px-3 py-3">PO number</th>
                    <th className="px-3 py-3">Status</th>
                    {!isRestricted && <th className="px-3 py-3">Document number</th>}
                    <th className="px-3 py-3">UTR number</th>
                    <th className="px-3 py-3">Value date</th>
                    <th className="px-3 py-3">Transaction description</th>
                    <th className="px-3 py-3 text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((row) => (
                    <tr key={row.po_number} className="border-b border-border/60 align-top">
                      <td className="px-3 py-3 font-semibold text-ink">{row.po_number}</td>
                      <td className="px-3 py-3"><OutcomeBadge outcome={row.outcome} /></td>
                      {!isRestricted && <td className="px-3 py-3 text-ink-dim">{row.document_number ?? '—'}</td>}
                      <td className="px-3 py-3 text-ink-dim">{row.utr_number ?? '—'}</td>
                      <td className="px-3 py-3 text-ink-dim">{row.value_date ? formatIndianDate(row.value_date) : '—'}</td>
                      <td className="max-w-md whitespace-pre-wrap break-words px-3 py-3 text-xs text-ink-dim">{row.transaction_description ?? '—'}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-ink">{row.transaction_amount == null ? '—' : formatIndianNumber(row.transaction_amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {results.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No PO numbers were recognized in the text above.</p>}
            </div>
          )}
        </GlassCard>

        {status.can_upload && (
          <GlassCard padding="lg" className="flex flex-col gap-4">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
              <div>
                <h2 className="font-display text-xl font-semibold text-ink">Bank statement uploads</h2>
                <p className="mt-1 text-sm text-ink-dim">Upload once - transactions accumulate here permanently. Re-uploading an overlapping date range never creates duplicates.</p>
              </div>
              <label className="inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 text-sm font-semibold text-ink">
                <Upload className="h-4 w-4" />{busy === 'upload' ? 'Processing…' : 'Upload statement'}
                <input
                  type="file"
                  accept=".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="sr-only"
                  disabled={Boolean(busy)}
                  onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadStatement(file); event.currentTarget.value = '' }}
                />
              </label>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[54rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-ink-faint">
                    <th className="px-2 py-3">Uploaded at (IST)</th>
                    <th className="px-2 py-3">Filename</th>
                    <th className="px-2 py-3">Account</th>
                    <th className="px-2 py-3">Date range</th>
                    <th className="px-2 py-3 text-right">Rows</th>
                    <th className="px-2 py-3 text-right">New</th>
                    <th className="px-2 py-3 text-right">Already present</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.map((row) => (
                    <tr key={row.id} className="border-b border-border/60 align-top">
                      <td className="whitespace-nowrap px-2 py-3 text-ink-dim">{row.uploaded_at ? formatIndianDateTime(row.uploaded_at) : '—'}</td>
                      <td className="max-w-xs truncate px-2 py-3 text-ink">{row.filename}</td>
                      <td className="px-2 py-3 text-ink-dim">{row.account_number ?? '—'}</td>
                      <td className="whitespace-nowrap px-2 py-3 text-ink-dim">{row.from_date ?? '—'} → {row.to_date ?? '—'}</td>
                      <td className="px-2 py-3 text-right tabular-nums text-ink-dim">{formatIndianNumber(row.row_count)}</td>
                      <td className="px-2 py-3 text-right tabular-nums text-ink">{formatIndianNumber(row.inserted_count)}</td>
                      <td className="px-2 py-3 text-right tabular-nums text-ink-faint">{formatIndianNumber(row.duplicate_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {uploads.length === 0 && <p className="py-8 text-center text-sm text-ink-faint">No bank statements have been uploaded yet.</p>}
            </div>
            <Pagination page={uploadPage} pageCount={Math.max(1, Math.ceil(uploadTotal / 10))} pageSize={10} totalItems={uploadTotal} itemLabel="uploads" onPageChange={setUploadPage} onPageSizeChange={() => undefined} />
          </GlassCard>
        )}
      </div>
    </AppShell>
  )
}
