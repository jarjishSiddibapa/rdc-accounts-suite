import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type ReactNode } from 'react'
import {
  AlertTriangle, CheckCircle2, Download, FileSpreadsheet, IndianRupee, Plus, RotateCcw, Search, Trash2, X, XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Modal } from '@/components/Modal'
import { SearchBox } from '@/components/SearchBox'
import { Pagination } from '@/components/Pagination'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { ApiError, apiUrl, del, get, post, postBlob, put } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { formatIndianCurrency, formatIndianDate, formatIndianDateTime } from '@/lib/regional'
import {
  BASE, PARTY_LIMIT, amountValue, blankEntry, cleanAccount, cleanCity, cleanIfsc, cleanParty, fileCode, isBlank,
  parsePasted, rowProblems, type CheckedRow, type Entry, type MadeFile, type ReviewRow, type Status,
} from '@/lib/hsbcTradepay'
import { cn } from '@/utils/cn'

const ERP_TIMEOUT_MS = 120_000
const FIELDS = ['vendor_code', 'invoice_no', 'amount'] as const
const FIELD_LABELS = { vendor_code: 'Vendor code', invoice_no: 'Invoice number', amount: 'Amount (net of TDS)' }

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
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

function Field({ label, children, note, className }: { label: string; children: ReactNode; note?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <span className="text-xs font-semibold text-ink-dim">{label}</span>
      {children}
      {note && <span className="text-xs leading-5 text-ink-faint">{note}</span>}
    </div>
  )
}

// ── one checked payment ────────────────────────────────────────────────────────────────────────────────────────────

function PaymentCard({ row, descriptions, onChange, onRemove }: {
  row: ReviewRow; descriptions: string[]; onChange: (change: Partial<ReviewRow>) => void; onRemove: () => void
}) {
  const problems = rowProblems(row)
  const account = row.accounts.find((a) => a.key === row.account_key)
  const blocked = row.errors.length > 0
  return (
    <li className={cn('flex flex-col gap-3 rounded-2xl border p-4',
      blocked ? 'border-red-500/40 bg-red-500/5' : 'border-border bg-surface/50')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">
            {row.line}. {row.vendor_name || `Vendor ${row.vendor_code || '(blank)'}`}
          </p>
          <p className="text-xs text-ink-dim">
            Vendor {row.vendor_code || '—'} · Invoice {row.invoice_no || '—'}
            {row.invoice && (
              <> · ERP: {row.invoice.status}
                {row.invoice.date && <>, dated {formatIndianDate(row.invoice.date)}</>}
                , amount {formatIndianCurrency(Number(row.invoice.amount))}
                , unpaid {formatIndianCurrency(Number(row.invoice.open))}
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {problems.length === 0 && row.warnings.length === 0 && <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-label="Ready" />}
          <span className="text-sm font-semibold text-ink">{blocked ? row.amount : formatIndianCurrency(amountValue(row.amount))}</span>
          <button type="button" className="grid h-9 w-9 place-items-center rounded-lg text-ink-faint transition hover:bg-red-500/10 hover:text-red-500"
            aria-label={`Leave payment ${row.line} out of the file`} title="Leave this payment out" onClick={onRemove}>
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {!blocked && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1.7fr_0.75fr_0.95fr_1.8fr]">
          <Field label="Party name" note={`${row.party_name.length} of ${PARTY_LIMIT} characters`}>
            <input className="field-control w-full" value={row.party_name} maxLength={PARTY_LIMIT} aria-label={`Party name, row ${row.line}`}
              onChange={(e) => onChange({ party_name: cleanParty(e.target.value) })} />
          </Field>
          <Field label="City">
            <input className="field-control w-full" value={row.city} aria-label={`City, row ${row.line}`}
              onChange={(e) => onChange({ city: cleanCity(e.target.value) })} />
          </Field>
          <Field label="Description of payment">
            <CreatableCombobox value={row.description} options={descriptions} ariaLabel={`Description, row ${row.line}`}
              suggestionLabel="Descriptions" onChange={(description) => onChange({ description })} />
          </Field>
          {row.manual_account ? (
            <Field label="Bank account (typed by you)">
              <div className="grid grid-cols-[1.2fr_1fr] gap-2">
                <input className="field-control w-full" placeholder="Account number" value={row.typed_account} inputMode="text"
                  aria-label={`Account number, row ${row.line}`} autoComplete="off"
                  onChange={(e) => onChange({ typed_account: cleanAccount(e.target.value) })} />
                <input className="field-control w-full" placeholder="IFSC" value={row.typed_ifsc} autoComplete="off"
                  aria-label={`IFSC, row ${row.line}`}
                  onChange={(e) => onChange({ typed_ifsc: cleanIfsc(e.target.value) })} />
              </div>
            </Field>
          ) : (
            <Field label="Bank account (from the ERP)" note={account && (
              <>{[account.bank, account.branch].filter(Boolean).join(', ')}{account.beneficiary && <> · in the name of {account.beneficiary}</>}</>
            )}>
              {row.accounts.length === 1 ? (
                <p className="field-control flex min-h-11 items-center font-mono text-sm">{row.accounts[0].account} · {row.accounts[0].ifsc}</p>
              ) : (
                <select className={cn('field-control w-full', !row.account_key && 'border-amber-500')} value={row.account_key}
                  aria-label={`Bank account, row ${row.line}`} onChange={(e) => onChange({ account_key: e.target.value })}>
                  <option value="">Choose one of {row.accounts.length} accounts…</option>
                  {row.accounts.map((a) => (
                    <option key={a.key} value={a.key}>
                      {a.account} · {a.ifsc}{a.bank ? ` · ${a.bank}` : ''}{a.locations.length ? ` · ${a.locations.join('/')}` : ''}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
        </div>
      )}

      {(row.errors.length > 0 || row.warnings.length > 0) && (
        <ul className="flex flex-col gap-1">
          {row.errors.map((text) => (
            <li key={text} className="flex items-start gap-2 text-xs font-medium text-red-500">
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{text}
            </li>
          ))}
          {row.warnings.map((text) => (
            <li key={text} className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{text}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

// ── the files made so far ──────────────────────────────────────────────────────────────────────────────────────────

function History({ version, onRemoved }: { version: number; onRemoved: () => void }) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [files, setFiles] = useState<MadeFile[]>([])
  const [total, setTotal] = useState(0)
  const [removing, setRemoving] = useState<MadeFile | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ q: query, page: String(page), page_size: String(pageSize) })
      const reply = await get<{ total: number; items: MadeFile[] }>(`${BASE}/files?${params}`)
      setFiles(reply.items)
      setTotal(reply.total)
      setError(null)
    } catch (err) {
      setError(message(err, 'The list of files could not be loaded.'))
    }
  }, [query, page, pageSize])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), query ? 300 : 0)
    return () => window.clearTimeout(timer)
  }, [load, query, version])

  async function remove() {
    if (!removing) return
    setBusy(true)
    try {
      await del(`${BASE}/files/${removing.id}`)
      setRemoving(null)
      await load()
      onRemoved()
    } catch (err) {
      setError(message(err, 'The file could not be removed.'))
      setRemoving(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="font-display text-lg font-semibold text-ink">Files made</h3>
        <SearchBox value={query} onChange={(v) => { setQuery(v); setPage(1) }} className="sm:w-80"
          placeholder="File code, invoice or vendor code" />
      </div>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {files.length === 0 ? (
        <p className="text-sm text-ink-dim">{query ? 'No file matches this search.' : 'No file has been made yet.'}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[40rem] text-sm">
            <thead className="bg-bg-soft/70 text-left text-xs text-ink-dim">
              <tr>
                {['File code', 'Date in file', 'Payments', 'Total', 'Made by', ''].map((h) => (
                  <th key={h} className="px-3 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.id} className="border-t border-border">
                  <td className="px-3 py-2 font-semibold text-ink">{f.file_code}</td>
                  <td className="px-3 py-2 text-ink-dim">{formatIndianDate(f.file_date)}</td>
                  <td className="px-3 py-2 text-ink-dim">{f.rows}</td>
                  <td className="px-3 py-2 text-ink-dim">{formatIndianCurrency(Number(f.total))}</td>
                  <td className="px-3 py-2 text-ink-dim">{f.made_by}<span className="block text-xs text-ink-faint">{formatIndianDateTime(f.made_at)}</span></td>
                  <td className="px-3 py-2">
                    {f.mine && (
                      <div className="flex justify-end gap-1">
                        <a className="grid h-9 w-9 place-items-center rounded-lg text-ink-dim transition hover:bg-bg-soft hover:text-accent"
                          href={apiUrl(`${BASE}/files/${f.id}/download`)} aria-label={`Download ${f.file_code}`} title="Download again">
                          <Download className="h-4 w-4" />
                        </a>
                        <button type="button" className="grid h-9 w-9 place-items-center rounded-lg text-ink-dim transition hover:bg-red-500/10 hover:text-red-500"
                          aria-label={`Remove ${f.file_code}`} title="Remove from this list" onClick={() => setRemoving(f)}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination page={page} pageCount={Math.max(1, Math.ceil(total / pageSize))} pageSize={pageSize} totalItems={total}
        onPageChange={setPage} onPageSizeChange={(size) => { setPageSize(size); setPage(1) }} itemLabel="files" />
      <Modal open={removing !== null} onClose={() => setRemoving(null)} title={`Remove ${removing?.file_code ?? ''}?`}>
        <div className="flex flex-col gap-4">
          <p className="text-sm leading-6 text-ink-dim">
            Use this for a file that was not uploaded to the bank. Its file code becomes free again and its invoices are
            no longer reported as already used. It does not cancel anything at the bank.
          </p>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="secondary" onClick={() => setRemoving(null)}>Keep it</Button>
            <Button variant="danger" loading={busy} onClick={() => void remove()}>Remove</Button>
          </div>
        </div>
      </Modal>
    </GlassCard>
  )
}

// ── the page ───────────────────────────────────────────────────────────────────────────────────────────────────────

export default function HsbcTradepay() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [status, setStatus] = useState<Status | null>(null)
  const [location, setLocation] = useState('')
  const [series, setSeries] = useState('1')
  const [entries, setEntries] = useState<Entry[]>(() => [blankEntry()])
  const [rows, setRows] = useState<ReviewRow[] | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [checking, setChecking] = useState(false)
  const [making, setMaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [made, setMade] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [idInput, setIdInput] = useState('')
  const [editId, setEditId] = useState(false)
  const [savingId, setSavingId] = useState(false)
  const reviewRef = useRef<HTMLDivElement>(null)

  // The next series number of the location (also after a file is made or removed).
  useEffect(() => {
    const loc = location.trim().toUpperCase()
    if (loc && !/^[A-Z]{2,3}$/.test(loc)) return
    const timer = window.setTimeout(() => {
      void get<Status>(`${BASE}/status?location=${encodeURIComponent(loc)}`).then((s) => {
        setStatus(s)
        if (!loc && s.location) setLocation(s.location)
        setSeries(String(s.next_series))
      }).catch((err) => setError(message(err, 'The page could not be loaded.')))
    }, loc ? 250 : 0)
    return () => window.clearTimeout(timer)
  }, [location, version])

  const code = fileCode(location, series)
  const filled = entries.filter((e) => !isBlank(e))

  function changeEntries(next: Entry[]) {
    // there is always an empty line to type or paste into; anything fetched earlier no longer matches
    if (!next.length || !isBlank(next[next.length - 1])) next = [...next, blankEntry()]
    setEntries(next)
    setRows(null)
    setMade(null)
  }

  function paste(event: ClipboardEvent<HTMLInputElement>, at: number, field: number) {
    const text = event.clipboardData.getData('text')
    if (!text.includes('\t') && !text.trim().includes('\n')) return
    event.preventDefault()
    const cells = parsePasted(text)
    const next = [...entries]
    cells.forEach((line, offset) => {
      const target = { ...(next[at + offset] ?? blankEntry()) }
      line.slice(0, FIELDS.length - field).forEach((cell, i) => { target[FIELDS[field + i]] = cell })
      next[at + offset] = target
    })
    changeEntries(next)
  }

  async function fetchDetails() {
    setError(null)
    setMade(null)
    if (status && filled.length > status.max_rows) {
      setError(`One file can hold up to ${status.max_rows} payments. Split the list into two files.`)
      return
    }
    setChecking(true)
    try {
      const reply = await post<{ rows: CheckedRow[] }>(`${BASE}/lookup`, {
        rows: filled.map(({ vendor_code, invoice_no, amount }) => ({ vendor_code, invoice_no, amount })),
      }, ERP_TIMEOUT_MS)
      setRows(reply.rows.map((r, i) => ({ ...r, entry_id: filled[i].id, typed_account: '', typed_ifsc: '' })))
      setAcknowledged(false)
      window.setTimeout(() => reviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
    } catch (err) {
      setError(message(err, 'The details could not be fetched.'))
    } finally {
      setChecking(false)
    }
  }

  const changeRow = (id: number, change: Partial<ReviewRow>) => {
    setRows((current) => current && current.map((r) => (r.entry_id === id ? { ...r, ...change } : r)))
    setAcknowledged(false)
  }

  /** Take one payment out of the list and out of the check, keeping what was chosen on the others. */
  function leaveOut(id: number) {
    const left = (rows ?? []).filter((r) => r.entry_id !== id).map((r, i) => ({ ...r, line: i + 1 }))
    setRows(left.length ? left : null)
    const kept = entries.filter((e) => e.id !== id)
    setEntries(kept.length && isBlank(kept[kept.length - 1]) ? kept : [...kept, blankEntry()])
    setAcknowledged(false)
  }

  const summary = useMemo(() => {
    const list = rows ?? []
    return {
      unready: list.filter((r) => rowProblems(r).length > 0).length,
      warned: list.filter((r) => r.warnings.length > 0).length,
      total: list.reduce((sum, r) => sum + (r.errors.length ? 0 : amountValue(r.amount)), 0),
    }
  }, [rows])

  const blockers: string[] = []
  if (status && !status.ready) blockers.push('The HSBC ID is not set yet (see the top of the page).')
  if (!code) blockers.push('Enter the location (2 or 3 letters) and the series number.')
  if (summary.unready) blockers.push(`${summary.unready} payment${summary.unready > 1 ? 's need' : ' needs'} attention above.`)
  if (summary.warned && !acknowledged) blockers.push('Tick that you have checked the warnings.')

  async function makeFile() {
    if (!rows || blockers.length) return
    setMaking(true)
    setError(null)
    try {
      const blob = await postBlob(`${BASE}/generate`, {
        location: location.trim().toUpperCase(),
        series: Number(series),
        rows: rows.map((r) => ({
          vendor_code: r.vendor_code, invoice_no: r.invoice_no, amount: r.amount, party_name: r.party_name.trim(),
          city: r.city.trim(), description: r.description.trim(), account_key: r.account_key,
          manual_account: r.typed_account, manual_ifsc: r.typed_ifsc,
        })),
      })
      saveFile(blob, `${code}.csv`)
      setMade(`${code}.csv`)
      // cleared on purpose: pressing the button twice must not put the same invoices into two files
      setEntries([blankEntry()])
      setRows(null)
      setVersion((v) => v + 1)
    } catch (err) {
      setError(message(err, 'The file could not be made.'))
    } finally {
      setMaking(false)
    }
  }

  async function saveId() {
    setSavingId(true)
    setError(null)
    try {
      await put(`${BASE}/settings`, { hsbc_id: idInput })
      setEditId(false)
      setVersion((v) => v + 1)
    } catch (err) {
      setError(message(err, 'The HSBC ID could not be saved.'))
    } finally {
      setSavingId(false)
    }
  }

  return (
    <AppShell title="RDC HSBC Tradepay CSV Generation">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl">
              <IndianRupee className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Accounts · Vendor payments</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                RDC HSBC Tradepay CSV Generation
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Enter the vendor code, invoice number and amount. The party name, city and bank account are read from the
                ERP; check them and download the upload file in the HSBC TradePay format.
              </p>
            </div>
          </div>

          {status && !status.ready && !isAdmin && (
            <p className="status-banner border-amber-500/30 bg-amber-500/8 text-amber-600 dark:text-amber-400">
              An administrator has to set the HSBC ID (cell E1 of the template) here once before files can be made.
            </p>
          )}
          {status && isAdmin && (
            status.ready && !editId ? (
              <p className="text-sm text-ink-dim">
                HSBC ID (cell E1): <span className="font-mono text-ink">{status.hsbc_id}</span>{' '}
                <button type="button" className="font-semibold text-accent hover:underline"
                  onClick={() => { setIdInput(status.hsbc_id); setEditId(true) }}>Change</button>
              </p>
            ) : (
              <div className="flex flex-col gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
                <label htmlFor="hsbc-id" className="text-sm font-semibold text-ink">
                  HSBC ID: the value in cell E1 of your HSBC template
                </label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input id="hsbc-id" className="field-control w-full sm:max-w-md" value={idInput} autoComplete="off"
                    onChange={(e) => setIdInput(e.target.value)} placeholder="Copy it from cell E1" />
                  <Button onClick={() => void saveId()} loading={savingId} disabled={!idInput.trim()}>Save</Button>
                  {status.ready && <Button variant="ghost" onClick={() => setEditId(false)}>Cancel</Button>}
                </div>
                <p className="text-xs text-ink-faint">Set once by an administrator; it goes into every file unchanged.</p>
              </div>
            )
          )}
        </GlassCard>

        {made && (
          <p role="status" className="status-banner flex items-start gap-2 border-emerald-500/30 bg-emerald-500/8 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <strong>{made}</strong> has been downloaded. Upload it to the bank as it is: opening and saving it in Excel
              can damage long account numbers.
            </span>
          </p>
        )}
        {error && <ErrorBanner>{error}</ErrorBanner>}

        <Step n={1} title="File code" hint="RDCC + location + series number. Each code can be used once.">
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1.4fr]">
            <Field label="Location (2 or 3 letters)">
              <CreatableCombobox value={location} options={status?.locations ?? []} suggestionLabel="Locations"
                ariaLabel="Location" placeholder="For example HYD"
                onChange={(v) => setLocation(v.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 3))} />
            </Field>
            <Field label="Series number">
              <input className="field-control w-full" inputMode="numeric" value={series} aria-label="Series number"
                onChange={(e) => setSeries(e.target.value.replace(/\D/g, '').slice(0, 3))} />
            </Field>
            <Field label="File code and date" note={status ? `The date in the file is today, ${formatIndianDate(status.today)}.` : undefined}>
              <p className="field-control flex min-h-11 items-center gap-2 font-mono text-sm font-semibold">
                <FileSpreadsheet className="h-4 w-4 text-accent" />{code ? `${code}.csv` : '—'}
              </p>
            </Field>
          </div>
        </Step>

        <Step n={2} title="Payments" hint="Type the rows, or copy the three columns in Excel and paste into the first box.">
          <div className="flex flex-col gap-2">
            <div className="hidden grid-cols-[2.5rem_1fr_1.4fr_1fr_2.75rem] gap-2 px-1 text-xs font-semibold text-ink-dim sm:grid">
              <span>#</span>{FIELDS.map((f) => <span key={f}>{FIELD_LABELS[f]}</span>)}<span />
            </div>
            <div className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto p-1">
              {entries.map((entry, index) => (
                <div key={entry.id} className="grid grid-cols-1 items-center gap-2 border-b border-border pb-2 sm:grid-cols-[2.5rem_1fr_1.4fr_1fr_2.75rem] sm:border-0 sm:pb-0">
                  <span className="text-xs text-ink-faint">{index + 1}</span>
                  {FIELDS.map((field, column) => (
                    <input key={field} className="field-control w-full" value={entry[field]} placeholder={FIELD_LABELS[field]}
                      aria-label={`${FIELD_LABELS[field]}, row ${index + 1}`} autoComplete="off"
                      inputMode={field === 'amount' ? 'decimal' : 'text'}
                      onPaste={(e) => paste(e, index, column)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && filled.length && !checking) void fetchDetails() }}
                      onChange={(e) => changeEntries(entries.map((x) => (x.id === entry.id ? { ...x, [field]: e.target.value } : x)))} />
                  ))}
                  <button type="button" aria-label={`Remove row ${index + 1}`} disabled={isBlank(entry)}
                    className="grid h-11 w-11 place-items-center justify-self-end rounded-lg text-ink-faint transition hover:bg-red-500/10 hover:text-red-500 disabled:invisible"
                    onClick={() => changeEntries(entries.filter((x) => x.id !== entry.id))}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Button onClick={() => void fetchDetails()} loading={checking} disabled={!filled.length}
              icon={<Search className="h-4 w-4" />}>Fetch details from ERP</Button>
            <Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={() => setEntries([...entries, blankEntry()])}>Add row</Button>
            <Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!filled.length} onClick={() => changeEntries([blankEntry()])}>Clear</Button>
            {filled.length > 0 && <span className="text-sm text-ink-dim sm:ml-auto">{filled.length} payment{filled.length > 1 ? 's' : ''} entered</span>}
          </div>
        </Step>

        {rows && (
          <div ref={reviewRef}>
            <Step n={3} title="Check and download" hint="These are the values that go into the file. Correct anything that is not right.">
              <ul className="flex flex-col gap-3">
                {rows.map((row) => (
                  <PaymentCard key={row.entry_id} row={row} descriptions={status?.descriptions ?? []}
                    onChange={(change) => changeRow(row.entry_id, change)} onRemove={() => leaveOut(row.entry_id)} />
                ))}
              </ul>
              <div className="flex flex-col gap-3 border-t border-border pt-4">
                <p className="text-sm text-ink">
                  <strong>{rows.length}</strong> payment{rows.length > 1 ? 's' : ''}, total <strong>{formatIndianCurrency(summary.total)}</strong>
                  {summary.warned > 0 && <span className="text-amber-600 dark:text-amber-400"> · {summary.warned} with warnings</span>}
                </p>
                {summary.warned > 0 && (
                  <label className="flex cursor-pointer items-start gap-2.5 text-sm text-ink">
                    <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={acknowledged}
                      onChange={(e) => setAcknowledged(e.target.checked)} />
                    I have checked the warnings above.
                  </label>
                )}
                {blockers.length > 0 && (
                  <ul className="list-disc pl-5 text-xs leading-5 text-ink-dim">
                    {blockers.map((b) => <li key={b}>{b}</li>)}
                  </ul>
                )}
                <div>
                  <Button onClick={() => void makeFile()} loading={making} disabled={blockers.length > 0}
                    icon={<Download className="h-4 w-4" />}>Download {code ? `${code}.csv` : 'CSV'}</Button>
                </div>
              </div>
            </Step>
          </div>
        )}

        <History version={version} onRemoved={() => setVersion((v) => v + 1)} />
      </div>
    </AppShell>
  )
}
