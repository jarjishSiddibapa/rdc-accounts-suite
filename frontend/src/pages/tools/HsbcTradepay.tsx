import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, IndianRupee, Plus, RotateCcw, Search, Upload } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { ApiError, apiUrl, get, post, postBlob, postForm } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import {
  BASE, amountValue, blankEntry, fileCode, isBlank, reviewRow, saveFile, type CheckedRow, type Entry, type ReviewRow,
  type Status,
} from '@/lib/hsbcTradepay'
import { formatIndianCurrency, formatIndianDate } from '@/lib/regional'
import { FileHistory } from './hsbc/FileHistory'
import { HsbcId } from './hsbc/HsbcId'
import { PaymentRows } from './hsbc/Payments'
import { Review } from './hsbc/Review'
import { Field, Step, message } from './hsbc/parts'

const ERP_TIMEOUT_MS = 120_000

interface Made { name: string; count: number; total: number; blob: Blob }

/** The filled rows as one text: the ERP check stays on screen only while this does not change. */
const filledText = (list: Entry[]) =>
  JSON.stringify(list.filter((e) => !isBlank(e)).map((e) => [e.id, e.vendor_code, e.invoice_no, e.amount]))

function reveal(element: HTMLElement | null, block: ScrollLogicalPosition = 'start') {
  window.setTimeout(() => element?.scrollIntoView({ behavior: 'smooth', block }), 50)
}

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
  const [uploading, setUploading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [entryError, setEntryError] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [makeError, setMakeError] = useState<string | null>(null)
  const [made, setMade] = useState<Made | null>(null)
  const [version, setVersion] = useState(0)
  const [uploaded, setUploaded] = useState<{ name: string; count: number; notes: string[] } | null>(null)
  const reviewRef = useRef<HTMLDivElement>(null)
  const madeRef = useRef<HTMLDivElement>(null)
  const entryErrorRef = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const firstLoad = useRef(true)
  const remembered = useRef(new Map<number, ReviewRow>())       // choices survive "fetch again" after a correction

  // The next series number of the location (also after a file is made or removed).
  useEffect(() => {
    const loc = location.trim().toUpperCase()
    if (loc && !/^[A-Z]{2,3}$/.test(loc)) return
    let stale = false
    const timer = window.setTimeout(() => {
      void get<Status>(`${BASE}/status?location=${encodeURIComponent(loc)}`).then((s) => {
        if (stale) return
        setStatus(s)
        setLoadError(null)
        if (firstLoad.current) {
          firstLoad.current = false
          if (!loc && s.location) {                             // start with the location used last
            setLocation(s.location)
            return
          }
        }
        setSeries(String(s.next_series))
      }).catch((err) => { if (!stale) setLoadError(message(err, 'The page could not be loaded.')) })
    }, loc ? 250 : 0)
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [location, version])

  useEffect(() => {
    rows?.forEach((r) => remembered.current.set(r.entry_id, r))
  }, [rows])

  const code = fileCode(location, series)
  const filled = entries.filter((e) => !isBlank(e))
  const busy = checking || making || uploading

  function changeEntries(next: Entry[]) {
    // there is always an empty line to type or paste into; anything fetched earlier no longer matches changed rows
    if (!next.length || !isBlank(next[next.length - 1])) next = [...next, blankEntry()]
    if (filledText(next) !== filledText(entries)) {
      setRows(null)
      setMade(null)
      setMakeError(null)
    }
    setEntries(next)
    setEntryError(null)
    setUploadError(null)
  }

  function addRow() {
    const last = entries[entries.length - 1]
    const target = last && isBlank(last) ? last : blankEntry()
    if (target !== last) setEntries([...entries, target])
    window.setTimeout(() => document.querySelector<HTMLInputElement>(`input[data-entry="${target.id}"]`)?.focus(), 0)
  }

  function clear() {
    changeEntries([blankEntry()])
    setUploaded(null)
    remembered.current.clear()
  }

  /** A problem with fetching, shown beside the button that was pressed. */
  function failed(text: string) {
    setEntryError(text)
    reveal(entryErrorRef.current, 'nearest')
  }

  /** The rows of a filled template take the place of the rows on the page. */
  async function upload(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setEntryError(null)
    setUploadError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const reply = await postForm<{ rows: Omit<Entry, 'id'>[]; notes: string[] }>(`${BASE}/upload`, form)
      remembered.current.clear()
      changeEntries(reply.rows.map((r) => blankEntry(r)))
      setUploaded({ name: file.name, count: reply.rows.length, notes: reply.notes })
    } catch (err) {
      setUploadError(message(err, 'The file could not be read.'))
    } finally {
      setUploading(false)
    }
  }

  async function fetchDetails() {
    if (busy || !filled.length) return
    setEntryError(null)
    setUploadError(null)
    setMakeError(null)
    setMade(null)
    if (status && filled.length > status.max_rows) {
      failed(`One file can hold up to ${status.max_rows} payments. Split the list into two files.`)
      return
    }
    setChecking(true)                                           // the page is locked until the answer is in
    try {
      const reply = await post<{ rows: CheckedRow[] }>(`${BASE}/lookup`, {
        rows: filled.map(({ vendor_code, invoice_no, amount }) => ({ vendor_code, invoice_no, amount })),
      }, ERP_TIMEOUT_MS)
      setRows(reply.rows.map((r, i) => reviewRow(r, filled[i].id, remembered.current.get(filled[i].id))))
      setAcknowledged(false)
      setUploaded(null)
      reveal(reviewRef.current)
    } catch (err) {
      failed(message(err, 'The details could not be fetched.'))
    } finally {
      setChecking(false)
    }
  }

  const changeRow = (id: number, change: Partial<ReviewRow>) => {
    setRows((current) => current && current.map((r) => (r.entry_id === id ? { ...r, ...change } : r)))
    setMakeError(null)
    // another account can bring another warning: tick again
    if ('account_key' in change || 'typed_account' in change || 'typed_ifsc' in change) setAcknowledged(false)
  }

  /** Take one payment out of the list and out of the check, keeping what was chosen on the others. */
  function leaveOut(id: number) {
    const left = (rows ?? []).filter((r) => r.entry_id !== id).map((r, i) => ({ ...r, line: i + 1 }))
    setRows(left.length ? left : null)
    const kept = entries.filter((e) => e.id !== id)
    setEntries(kept.length && isBlank(kept[kept.length - 1]) ? kept : [...kept, blankEntry()])
    remembered.current.delete(id)
    setMakeError(null)
  }

  const blockers: string[] = []
  if (status && !status.ready) blockers.push('The HSBC ID is not set yet (see the top of the page).')
  if (!code) blockers.push('Enter the location (2 or 3 letters) and the series number in step 1.')

  async function makeFile() {
    if (!rows || busy || blockers.length) return
    setMaking(true)
    setMakeError(null)
    const name = `${code}.csv`
    try {
      const blob = await postBlob(`${BASE}/generate`, {
        location: location.trim().toUpperCase(),
        series: Number(series),
        rows: rows.map((r) => ({
          vendor_code: r.vendor_code, invoice_no: r.invoice_no, amount: r.amount, party_name: r.party_name.trim(),
          city: r.city.trim(), description: r.description.trim(), account_key: r.account_key,
          manual_account: r.typed_account, manual_ifsc: r.typed_ifsc, earlier_files: r.earlier_files,
        })),
      })
      saveFile(blob, name)
      setMade({
        name, blob, count: rows.length, total: rows.reduce((sum, r) => sum + amountValue(r.amount), 0),
      })
      // cleared on purpose: pressing the button twice must not put the same invoices into two files
      setEntries([blankEntry()])
      setRows(null)
      setUploaded(null)
      remembered.current.clear()
      setVersion((v) => v + 1)
      reveal(madeRef.current, 'center')
    } catch (err) {
      let text = message(err, 'The file could not be made.')
      if (err instanceof ApiError && err.status === 409 && text.startsWith('File code')) {
        text += ' The series number in step 1 has been updated: check it and download again.'
        setVersion((v) => v + 1)
      }
      setMakeError(text)
      // the answer may have been lost after the file was recorded: the list shows it
      if (err instanceof ApiError && (err.status === 0 || err.status === 408)) setVersion((v) => v + 1)
    } finally {
      setMaking(false)
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
          {status && <HsbcId status={status} isAdmin={isAdmin} onSaved={() => setVersion((v) => v + 1)} />}
        </GlassCard>

        {loadError && <ErrorBanner>{loadError}</ErrorBanner>}

        {/* locked while the ERP is read or the file is made, so what is on screen is what goes into the file */}
        <fieldset disabled={busy} className="flex min-w-0 flex-col gap-6">
          <Step n={1} title="File code" hint="RDCC + location + series number. Each code can be used once.">
            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1.4fr]">
              <Field label="Location (2 or 3 letters)">
                <CreatableCombobox value={location} options={status?.locations ?? []} suggestionLabel="Locations"
                  ariaLabel="Location" placeholder="For example HYD" showAllOnOpen
                  onChange={(v) => { setLocation(v.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 3)); setMakeError(null) }} />
              </Field>
              <Field label="Series number" note="The next free number is filled in; change it if needed.">
                <input className="field-control w-full" inputMode="numeric" value={series} aria-label="Series number"
                  onChange={(e) => { setSeries(e.target.value.replace(/\D/g, '').slice(0, 3)); setMakeError(null) }} />
              </Field>
              <Field label="File code and date" note={status ? `The date in the file is today, ${formatIndianDate(status.today)}.` : undefined}>
                <p className="field-control flex min-h-11 items-center gap-2 font-mono text-sm font-semibold">
                  <FileSpreadsheet className="h-4 w-4 text-accent" />{code ? `${code}.csv` : '—'}
                </p>
              </Field>
            </div>
          </Step>

          <Step n={2} title="Payments"
            hint="Upload the filled Excel template, type the rows, or copy the three columns in Excel and paste into the first box.">
            <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface/50 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-ink">Fill the payments in Excel</p>
                <p className="text-xs leading-5 text-ink-dim">Uploading a filled template replaces the rows below.</p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
                <a href={apiUrl(`${BASE}/template`)} download
                  className="inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-accent hover:underline">
                  <Download className="h-4 w-4" />Download Excel template
                </a>
                <Button variant="secondary" icon={<Upload className="h-4 w-4" />} loading={uploading}
                  onClick={() => fileInput.current?.click()}>Upload filled Excel</Button>
                <input ref={fileInput} type="file" accept=".xlsx,.xlsm,.xlsb,.xls" hidden aria-label="Filled Excel template"
                  onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = '' }} />
              </div>
            </div>
            {uploadError && <ErrorBanner>{uploadError}</ErrorBanner>}
            {uploaded && (
              <div role="status" className="flex flex-col gap-1 text-sm">
                <p className="flex items-start gap-2 text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    {uploaded.count} payment{uploaded.count > 1 ? 's' : ''} read from <strong className="[overflow-wrap:anywhere]">{uploaded.name}</strong>.
                    Choose Fetch details from ERP.
                  </span>
                </p>
                {uploaded.notes.length > 0 && (
                  <ul className="flex max-h-32 flex-col gap-1 overflow-y-auto">
                    {uploaded.notes.map((note) => (
                      <li key={note} className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{note}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            <PaymentRows entries={entries} onChange={changeEntries} onEnter={() => void fetchDetails()} />
            <div ref={entryErrorRef}>{entryError && <ErrorBanner>{entryError}</ErrorBanner>}</div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button onClick={() => void fetchDetails()} loading={checking} disabled={!filled.length}
                icon={<Search className="h-4 w-4" />}>Fetch details from ERP</Button>
              <Button variant="secondary" icon={<Plus className="h-4 w-4" />} onClick={addRow}>Add row</Button>
              <Button variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!filled.length} onClick={clear}>Clear</Button>
              {filled.length > 0 && <span className="text-sm text-ink-dim sm:ml-auto">{filled.length} payment{filled.length > 1 ? 's' : ''} entered</span>}
            </div>
          </Step>

          {rows && (
            <div ref={reviewRef}>
              <Review rows={rows} descriptions={status?.descriptions ?? []} fileName={code ? `${code}.csv` : ''}
                blockers={blockers} acknowledged={acknowledged} making={making} error={makeError}
                onAcknowledge={setAcknowledged} onChange={changeRow} onRemove={leaveOut}
                onDownload={() => void makeFile()} />
            </div>
          )}
        </fieldset>

        <div ref={madeRef} className={made ? undefined : 'hidden'}>
          {made && (
            <div role="status" className="status-banner flex flex-col gap-3 border-emerald-500/30 bg-emerald-500/8 sm:flex-row sm:items-center sm:justify-between">
              <p className="flex items-start gap-2 text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  <strong>{made.name}</strong> has been downloaded: {made.count} payment{made.count > 1 ? 's' : ''},
                  total {formatIndianCurrency(made.total)}. Upload it to the bank as it is: opening and saving it in Excel
                  can damage long account numbers.
                </span>
              </p>
              <Button variant="secondary" className="shrink-0" icon={<Download className="h-4 w-4" />}
                onClick={() => saveFile(made.blob, made.name)}>Download again</Button>
            </div>
          )}
        </div>

        <FileHistory version={version} onRemoved={() => setVersion((v) => v + 1)} />
      </div>
    </AppShell>
  )
}
