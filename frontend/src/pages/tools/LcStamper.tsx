import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  AlertTriangle, CheckCircle2, ClipboardPaste, Cpu, Download, FileSpreadsheet, FileText, Filter as FilterIcon,
  Globe, Loader2, Play, RotateCcw, ScanText, ShieldCheck, Stamp as StampIcon, Trash2, Upload, Wand2,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { StampCanvas } from '@/components/lc-stamper/StampCanvas'
import { ResultsPanel } from '@/components/lc-stamper/ResultsPanel'
import { ApiError, apiUrl, del, get, post, postForm } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import {
  BASE, COLOR_PRESETS, PLACEHOLDERS, formatSize, hexToRgb, loadSettings, readability, rgbToHex, saveSettings,
  type Capabilities, type ListSummary, type Mode, type OcrMode, type RunResult, type Settings, type StagedDocument,
} from '@/lib/lcStamper'
import { cn } from '@/utils/cn'

// ── small building blocks ──────────────────────────────────────────────────────────────────────────────────────────

function Step({ n, title, hint, children, done }: { n: number; title: string; hint?: string; children: ReactNode; done?: boolean }) {
  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold',
          done ? 'bg-emerald-500 text-white' : 'bg-accent text-accent-ink')}>
          {done ? <CheckCircle2 className="h-4 w-4" /> : n}
        </span>
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold leading-8 text-ink">{title}</h3>
          {hint && <p className="text-sm text-ink-dim">{hint}</p>}
        </div>
      </div>
      {children}
    </GlassCard>
  )
}

function Switch({ checked, onChange, label, hint, disabled }: {
  checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string; disabled?: boolean
}) {
  return (
    <label className={cn('flex cursor-pointer items-start gap-3 rounded-lg py-1.5', disabled && 'cursor-not-allowed opacity-50')}>
      <button type="button" role="switch" aria-checked={checked} disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn('relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition',
          checked ? 'border-accent bg-accent' : 'border-border bg-bg-soft')}>
        <span className={cn('absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all', checked ? 'left-[1.375rem]' : 'left-0.5')} />
      </button>
      <span className="min-w-0 text-sm">
        <span className="font-medium text-ink">{label}</span>
        {hint && <span className="block text-xs text-ink-dim">{hint}</span>}
      </span>
    </label>
  )
}

function Choice<T extends string>({ value, options, onChange, label }: {
  value: T; onChange: (value: T) => void; label: string
  options: { value: T; title: string; hint: string; icon?: ReactNode; badge?: string }[]
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn('grid gap-2', options.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2')}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={cn('flex flex-col items-start gap-1 rounded-xl border p-3 text-left transition',
            value === o.value ? 'border-accent bg-accent/10 shadow-sm' : 'border-border bg-surface hover:border-accent/50')}>
          <span className="flex w-full items-center gap-2 text-sm font-semibold text-ink">
            {o.icon}{o.title}
            {o.badge && <span className="ml-auto rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-emerald-600 dark:text-emerald-400">{o.badge}</span>}
          </span>
          <span className="text-xs leading-5 text-ink-dim">{o.hint}</span>
        </button>
      ))}
    </div>
  )
}

const TONE = { good: 'text-emerald-600 dark:text-emerald-400', warn: 'text-amber-600 dark:text-amber-400', mixed: 'text-sky-600 dark:text-sky-400' }

interface JobResponse { status: JobStatus; progress: number; phase: string; result: RunResult | null; error: string | null }

async function pollRun(jobId: string): Promise<JobState<RunResult>> {
  const job = await get<JobResponse>(`${BASE}/jobs/${jobId}`)
  return { status: job.status, progress: (job.progress ?? 0) * 100, phase: job.phase, result: job.result ?? undefined, error: job.error ?? undefined }
}

const UPLOAD_PARALLEL = 3

// ── the page ───────────────────────────────────────────────────────────────────────────────────────────────────────

export default function LcStamper() {
  const [settings, setSettings] = useState<Settings>(loadSettings)
  const [docs, setDocs] = useState<StagedDocument[]>([])
  const [uploading, setUploading] = useState<string[]>([])
  const [list, setList] = useState<ListSummary | null>(null)
  const [listTab, setListTab] = useState<'file' | 'paste'>('file')
  const [pasted, setPasted] = useState('')
  const [listBusy, setListBusy] = useState(false)
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<RunResult | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [overflow, setOverflow] = useState(false)
  const [dragging, setDragging] = useState(false)
  const resultsRef = useRef<HTMLDivElement>(null)
  const pdfInput = useRef<HTMLInputElement>(null)
  const listInput = useRef<HTMLInputElement>(null)
  const textArea = useRef<HTMLTextAreaElement>(null)

  const { mode, rules, stamp, output } = settings
  const running = Boolean(jobId) && !result
  const patch = useCallback((change: Partial<Settings>) => setSettings((s) => ({ ...s, ...change })), [])
  const patchRules = (change: Partial<Settings['rules']>) => setSettings((s) => ({ ...s, rules: { ...s.rules, ...change } }))
  const patchStamp = useCallback((change: Partial<Settings['stamp']>) => setSettings((s) => ({ ...s, stamp: { ...s.stamp, ...change } })), [])
  const moveStamp = useCallback((rel_x: number, rel_y: number) => patchStamp({ rel_x, rel_y }), [patchStamp])

  useEffect(() => saveSettings(settings), [settings])
  useEffect(() => {
    void get<Capabilities>(`${BASE}/capabilities`).then(setCaps).catch(() => undefined)
    void get<{ documents: StagedDocument[] }>(`${BASE}/documents`).then((r) => setDocs(r.documents)).catch(() => undefined)
  }, [])
  useEffect(() => {
    if (result) resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [result])

  // ── documents ────────────────────────────────────────────────────────────────────────────────────────────────────
  async function addDocuments(files: File[]) {
    const pdfs = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'))
    if (pdfs.length !== files.length) setError('Only PDF files can be added here. The invoice list goes in step 2.')
    else setError(null)
    const queue = [...pdfs]
    setUploading((u) => [...u, ...pdfs.map((f) => f.name)])
    const worker = async () => {
      for (let file = queue.shift(); file; file = queue.shift()) {
        try {
          const form = new FormData()
          form.append('files', file)
          const reply = await postForm<{ documents: StagedDocument[]; problems: { name: string; error: string }[] }>(`${BASE}/documents`, form)
          setDocs((d) => [...d, ...reply.documents])
          if (reply.problems.length) setError(reply.problems.map((p) => p.error).join(' '))
        } catch (err) {
          setError(err instanceof ApiError ? err.message : `"${file.name}" could not be uploaded.`)
        } finally {
          setUploading((u) => { const i = u.indexOf(file.name); return i < 0 ? u : [...u.slice(0, i), ...u.slice(i + 1)] })
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(UPLOAD_PARALLEL, pdfs.length) }, worker))
  }

  async function removeDoc(id: string) {
    setDocs((d) => d.filter((x) => x.id !== id))
    try { await del(`${BASE}/documents/${id}`) } catch { /* it expires by itself */ }
  }

  // ── the list ─────────────────────────────────────────────────────────────────────────────────────────────────────
  async function useListFile(file: File | undefined) {
    if (!file) return
    setListBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      setList(await postForm<ListSummary>(`${BASE}/list`, form))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The list could not be read.')
    } finally {
      setListBusy(false)
    }
  }

  async function usePasted() {
    setListBusy(true)
    setError(null)
    try {
      setList(await post<ListSummary>(`${BASE}/list/paste`, { text: pasted }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The numbers could not be read.')
    } finally {
      setListBusy(false)
    }
  }

  // ── what the list allows ─────────────────────────────────────────────────────────────────────────────────────────
  const canDates = Boolean(list?.has_dates)
  const canAmounts = Boolean(list?.has_amounts)
  const filterMode = mode === 'filter'
  const totalPages = docs.reduce((n, d) => n + d.pages, 0)
  const scanned = docs.filter((d) => d.sampled > 0 && d.text_pages < d.sampled).length

  const blockers = useMemo(() => {
    const list_: string[] = []
    if (docs.length === 0) list_.push('Add at least one PDF.')
    if (uploading.length) list_.push('Wait for the uploads to finish.')
    if (filterMode && !list) list_.push('Filter only needs an invoice list: upload one or type the numbers.')
    if (!filterMode && !stamp.text.trim()) list_.push('Type the text to stamp (or switch to Filter only).')
    return list_
  }, [docs.length, uploading.length, filterMode, list, stamp.text])

  async function run() {
    setStarting(true)
    setError(null)
    setResult(null)
    try {
      const effective = {
        ...rules,
        match_dates: rules.match_dates && canDates,
        match_amounts: rules.match_amounts && canAmounts,
      }
      const reply = await post<{ job_id: string }>(`${BASE}/run`, {
        document_ids: docs.map((d) => d.id), list_id: list?.id ?? null, mode, output, rules: effective, stamp,
      })
      setJobId(reply.job_id)
    } catch (err) {
      setJobId(null)
      setError(err instanceof ApiError ? err.message : 'The run could not be started.')
    } finally {
      setStarting(false)
    }
  }

  function insertPlaceholder(token: string) {
    const area = textArea.current
    if (!area) return patchStamp({ text: stamp.text + token })
    const start = area.selectionStart ?? stamp.text.length
    const end = area.selectionEnd ?? start
    patchStamp({ text: stamp.text.slice(0, start) + token + stamp.text.slice(end) })
    window.requestAnimationFrame(() => { area.focus(); area.setSelectionRange(start + token.length, start + token.length) })
  }

  const stampCard = (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent text-sm font-bold text-accent-ink">4</span>
        <div>
          <h3 className="font-display text-lg font-semibold leading-8 text-ink">Stamp</h3>
          <p className="text-sm text-ink-dim">Type the stamp, then drag it where you want it on the page.</p>
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="lc-stamp-text" className="text-sm font-medium text-ink">Text</label>
        <textarea id="lc-stamp-text" ref={textArea} rows={2} value={stamp.text} maxLength={600}
          onChange={(e) => patchStamp({ text: e.target.value })}
          placeholder="e.g. APPROVED FOR PAYMENT — LC {invoice}"
          className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink" />
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="flex items-center gap-1 text-xs text-ink-faint"><Wand2 className="h-3.5 w-3.5" />Fill from each invoice:</span>
          {PLACEHOLDERS.map((p) => (
            <button key={p.token} type="button" title={p.hint} onClick={() => insertPlaceholder(p.token)}
              className="rounded-full border border-border bg-surface px-2.5 py-1 font-mono text-xs text-accent transition hover:border-accent">
              {p.token}
            </button>
          ))}
          {list?.columns.slice(0, 6).map((column) => (
            <button key={column} type="button" title={`The "${column}" column of your list`}
              onClick={() => insertPlaceholder(`{${column}}`)}
              className="rounded-full border border-dashed border-border bg-surface px-2.5 py-1 font-mono text-xs text-accent transition hover:border-accent">
              {`{${column}}`}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        <label className="flex flex-col gap-1 text-sm font-medium text-ink">
          Size (pt)
          <input type="number" min={4} max={120} step={0.5} value={stamp.font_size}
            onChange={(e) => patchStamp({ font_size: Math.min(120, Math.max(4, Number(e.target.value) || 12)) })}
            className="h-10 w-24 rounded-lg border border-border bg-surface px-2 text-sm text-ink" />
        </label>
        <div className="flex flex-col gap-1 text-sm font-medium text-ink">
          Colour
          <div className="flex items-center gap-1.5">
            {COLOR_PRESETS.map((c) => (
              <button key={c.name} type="button" title={c.name} aria-label={c.name}
                aria-pressed={rgbToHex(c.rgb) === rgbToHex(stamp.color)}
                onClick={() => patchStamp({ color: c.rgb })}
                className={cn('h-8 w-8 rounded-full border-2 transition', rgbToHex(c.rgb) === rgbToHex(stamp.color) ? 'border-accent' : 'border-border')}
                style={{ background: rgbToHex(c.rgb) }} />
            ))}
            <input type="color" aria-label="Custom colour" value={rgbToHex(stamp.color)}
              onChange={(e) => patchStamp({ color: hexToRgb(e.target.value) })}
              className="h-8 w-9 cursor-pointer rounded-md border border-border bg-surface p-0.5" />
          </div>
        </div>
        <Switch checked={stamp.bold} onChange={(bold) => patchStamp({ bold })} label="Bold" />
      </div>
      <StampCanvas documents={docs} listId={list?.id ?? null} stamp={stamp} showStamp onMove={moveStamp} onOverflow={setOverflow} />
    </GlassCard>
  )

  const runCard = (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="font-display text-lg font-semibold text-ink">
          {filterMode ? 'Pick out the pages' : 'Stamp the pages'}
        </h3>
        <p className="text-sm text-ink-dim">
          {docs.length
            ? `${formatIndianNumber(docs.length)} PDF${docs.length === 1 ? '' : 's'} · ${formatIndianNumber(totalPages)} pages${list ? ` · ${formatIndianNumber(list.unique)} invoices in the list` : ' · no list: every page with content is kept'}`
            : 'Add your PDFs to begin.'}
        </p>
      </div>
      {blockers.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-lg border border-border bg-bg-soft/60 p-3 text-sm text-ink-dim">
          {blockers.map((b) => <li key={b} className="flex items-start gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />{b}</li>)}
        </ul>
      )}
      {!filterMode && overflow && (
        <p className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-600 dark:text-red-400">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />The stamp runs off the page. It will be cut off in the PDF.
        </p>
      )}
      {error && <ErrorBanner>{error}</ErrorBanner>}
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        {(jobId || result) && (
          <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={running}
            onClick={() => { setJobId(null); setResult(null); setError(null) }}>New run</Button>
        )}
        <Button icon={<Play className="h-4 w-4" />} loading={starting} disabled={blockers.length > 0 || running} onClick={() => void run()}>
          {filterMode ? 'Filter' : 'Stamp'} {docs.length ? `${formatIndianNumber(docs.length)} PDF${docs.length === 1 ? '' : 's'}` : ''}
        </Button>
      </div>
    </GlassCard>
  )

  return (
    <AppShell title="RDC LC Stamper Application">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl"><StampIcon className="h-5 w-5" /></span>
            <div>
              <p className="text-sm font-semibold text-accent">Invoice pages, checked and stamped</p>
              <h2 className="mt-1 font-display text-xl font-semibold tracking-[-0.025em] text-ink">RDC LC Stamper</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Find the invoices of your list in one or many PDFs, check their date and amount, and stamp only those pages
                — or just pick them out.
              </p>
            </div>
          </div>
          <Choice<Mode> label="What do you want to do?" value={mode} onChange={(m) => patch({ mode: m })}
            options={[
              { value: 'stamp', title: 'Stamp invoices', icon: <StampIcon className="h-4 w-4 text-accent" />,
                hint: 'Keep the pages of your invoices, stamp them, and get a verification report with the date and amount checks.' },
              { value: 'filter', title: 'Filter only', icon: <FilterIcon className="h-4 w-4 text-accent" />,
                hint: 'Just pick the pages out, no stamp. Needs only invoice numbers; the report says found or not found, and where.' },
            ]} />
        </GlassCard>

        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,32rem)]">
          <div className="flex min-w-0 flex-col gap-6">
            {/* 1 ── documents */}
            <Step n={1} title="PDF documents" done={docs.length > 0}
              hint="Add as many as you like. They are read and stamped side by side, each one separately.">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => { e.preventDefault(); setDragging(false); void addDocuments(Array.from(e.dataTransfer.files)) }}
                className={cn('flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-6 text-center transition',
                  dragging ? 'border-accent bg-accent/10' : 'border-border bg-surface/60')}>
                <Upload className="h-6 w-6 text-accent" />
                <p className="text-sm font-medium text-ink">Drop PDFs here</p>
                <Button variant="secondary" onClick={() => pdfInput.current?.click()}>Choose PDFs</Button>
                <p className="text-xs text-ink-faint">Up to {caps?.max_upload_mb ?? 600} MB each · kept for {caps?.retention_hours ?? 24} hours so you can run again with other settings</p>
                <input ref={pdfInput} type="file" accept=".pdf,application/pdf" multiple hidden
                  onChange={(e) => { void addDocuments(Array.from(e.target.files ?? [])); e.target.value = '' }} />
              </div>
              {(docs.length > 0 || uploading.length > 0) && (
                <ul className="flex flex-col gap-2">
                  {docs.map((d) => {
                    const r = readability(d)
                    return (
                      <li key={d.id} className="subpanel flex items-center gap-3 px-3 py-2">
                        <FileText className="h-5 w-5 shrink-0 text-red-500" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-ink" title={d.name}>{d.name}</span>
                          <span className="text-xs text-ink-faint">{formatIndianNumber(d.pages)} pages · {formatSize(d.size)} · <span className={TONE[r.tone]}>{r.label}</span></span>
                        </span>
                        <button type="button" aria-label={`Remove ${d.name}`} onClick={() => void removeDoc(d.id)}
                          className="grid h-9 w-9 place-items-center rounded-lg text-ink-faint transition hover:bg-red-500/10 hover:text-red-500">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </li>
                    )
                  })}
                  {uploading.map((name, i) => (
                    <li key={`${name}-${i}`} className="subpanel flex items-center gap-3 px-3 py-2 text-sm text-ink-dim">
                      <Loader2 className="h-5 w-5 shrink-0 animate-spin text-accent" /><span className="min-w-0 truncate">Uploading {name}…</span>
                    </li>
                  ))}
                </ul>
              )}
              {scanned > 0 && (
                <p className="flex items-start gap-2 text-xs text-ink-dim"><ScanText className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                  {scanned} of your PDFs {scanned === 1 ? 'looks' : 'look'} scanned. Those pages are read by OCR, which is slower than reading a text PDF.</p>
              )}
            </Step>

            {/* 2 ── the list */}
            <Step n={2} title={filterMode ? 'Invoice numbers' : 'Invoice list'} done={Boolean(list)}
              hint={filterMode ? 'One invoice number per row is enough.' : 'Invoice numbers, and optionally their date and amount to check.'}>
              <div className="flex gap-1 rounded-lg bg-bg-soft p-1" role="tablist">
                {([['file', 'Excel / CSV file', FileSpreadsheet], ['paste', 'Type or paste numbers', ClipboardPaste]] as const).map(([key, label, Icon]) => (
                  <button key={key} type="button" role="tab" aria-selected={listTab === key} onClick={() => setListTab(key)}
                    className={cn('flex min-h-10 flex-1 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition',
                      listTab === key ? 'bg-surface text-ink shadow-sm' : 'text-ink-dim hover:text-ink')}>
                    <Icon className="h-4 w-4" />{label}
                  </button>
                ))}
              </div>
              {listTab === 'file' ? (
                <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center">
                  <Button variant="secondary" loading={listBusy} icon={<Upload className="h-4 w-4" />} onClick={() => listInput.current?.click()}>
                    {list && list.name !== 'Typed invoice numbers' ? 'Replace the list' : 'Choose the list'}
                  </Button>
                  <input ref={listInput} type="file" accept=".xlsx,.xls,.csv" hidden
                    onChange={(e) => { void useListFile(e.target.files?.[0]); e.target.value = '' }} />
                  <a href={apiUrl(`${BASE}/templates/${filterMode ? 'filter' : 'full'}`)} download
                    className="inline-flex min-h-10 items-center gap-2 text-sm font-medium text-accent hover:underline">
                    <Download className="h-4 w-4" />Download the {filterMode ? 'filter-only' : 'invoice list'} template
                  </a>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <textarea rows={5} value={pasted} onChange={(e) => setPasted(e.target.value)} aria-label="Invoice numbers"
                    placeholder={'One per line, or separated by commas:\nINV-1001\nINV-1002'}
                    className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm text-ink" />
                  <div className="flex justify-end">
                    <Button variant="secondary" loading={listBusy} disabled={!pasted.trim()} onClick={() => void usePasted()}>Use these numbers</Button>
                  </div>
                </div>
              )}
              {list && (
                <div className="subpanel flex flex-col gap-3 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    <span className="font-medium text-ink">{list.name}</span>
                    <span className="text-ink-dim">· {formatIndianNumber(list.unique)} invoices</span>
                    {list.duplicates > 0 && <span className="text-ink-faint">({formatIndianNumber(list.duplicates)} repeated rows ignored)</span>}
                    <button type="button" onClick={() => setList(null)} className="ml-auto text-xs text-ink-faint hover:text-red-500">Remove</button>
                  </div>
                  <div className="flex flex-wrap gap-1.5 text-xs">
                    <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-emerald-700 dark:text-emerald-300">Invoice numbers ✓</span>
                    <span className={cn('rounded-full px-2.5 py-1', list.has_dates ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-bg-soft text-ink-faint')}>
                      Dates {list.has_dates ? '✓' : 'not in the list'}</span>
                    <span className={cn('rounded-full px-2.5 py-1', list.has_amounts ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-bg-soft text-ink-faint')}>
                      Amounts {list.has_amounts ? '✓' : 'not in the list'}</span>
                    {list.columns.length > 0 && <span className="rounded-full bg-bg-soft px-2.5 py-1 text-ink-dim">Extra columns: {list.columns.slice(0, 4).join(', ')}{list.columns.length > 4 ? '…' : ''}</span>}
                  </div>
                  <table className="w-full text-left text-xs">
                    <tbody className="divide-y divide-border">
                      {list.sample.map((s) => (
                        <tr key={s.invoice}><td className="py-1 pr-3 font-medium text-ink">{s.invoice}</td>
                          <td className="py-1 pr-3 tabular-nums text-ink-dim">{s.date ?? ''}</td>
                          <td className="py-1 text-right tabular-nums text-ink-dim">{s.amount !== null ? s.amount.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : ''}</td></tr>
                      ))}
                    </tbody>
                  </table>
                  {list.warnings.map((w) => <p key={w} className="text-xs text-amber-600 dark:text-amber-400">{w}</p>)}
                </div>
              )}
              {!list && !filterMode && (
                <p className="text-xs text-ink-faint">No list? Every page that has something on it is kept and stamped.</p>
              )}
            </Step>

            {/* 3 ── checks */}
            <Step n={3} title="How pages are checked" hint="The invoice number is always matched. Add the checks you need.">
              <div className="flex flex-col gap-1 divide-y divide-border">
                <div className="flex flex-col gap-1 pb-2">
                  <p className="flex items-center gap-2 text-sm font-medium text-ink"><ShieldCheck className="h-4 w-4 text-emerald-500" />Invoice number<span className="rounded-full bg-bg-soft px-2 py-0.5 text-[10px] font-semibold uppercase text-ink-dim">always</span></p>
                  <p className="text-xs text-ink-dim">Only the whole number counts: 1001 is not found inside 10012.</p>
                </div>
                <div className="py-2">
                  <Switch checked={rules.match_dates && canDates} disabled={!canDates} onChange={(v) => patchRules({ match_dates: v })}
                    label="Leave out pages whose invoice date differs from the list"
                    hint={canDates ? 'Off: the page is kept and the difference is flagged in the report.' : 'Your list has no invoice dates, so only the number is checked.'} />
                </div>
                <div className="flex flex-col gap-1 py-2">
                  <Switch checked={rules.match_amounts && canAmounts} disabled={!canAmounts} onChange={(v) => patchRules({ match_amounts: v })}
                    label="Leave out pages whose amount differs from the list"
                    hint={canAmounts ? 'Off: the page is kept and the difference is flagged in the report.' : 'Your list has no amounts, so only the number is checked.'} />
                  {canAmounts && (
                    <label className="ml-14 flex items-center gap-2 text-xs text-ink-dim">Allow a difference of up to ₹
                      <input type="number" min={0} step={0.5} value={rules.amount_tolerance}
                        onChange={(e) => patchRules({ amount_tolerance: Math.max(0, Number(e.target.value) || 0) })}
                        className="h-8 w-20 rounded-md border border-border bg-surface px-2 text-sm text-ink" /> (rounding)
                    </label>
                  )}
                </div>
                <div className="flex flex-col gap-3 py-3">
                  <p className="flex items-center gap-2 text-sm font-medium text-ink"><ScanText className="h-4 w-4 text-accent" />Reading the pages</p>
                  <Choice<OcrMode> label="OCR" value={rules.ocr_mode} onChange={(v) => patchRules({ ocr_mode: v })}
                    options={[
                      { value: 'auto', title: 'Smart', badge: 'Recommended', hint: "Reads the PDF's own text. OCR only for a page that is a scan or has unreadable text." },
                      { value: 'always', title: 'Always OCR', hint: 'Every page is OCR-ed. Slow; only for PDFs whose text is wrong.' },
                      { value: 'off', title: 'Never OCR', hint: 'Only text PDFs. Scanned pages are not matched.' },
                    ]} />
                  {rules.ocr_mode !== 'off' && (
                    <div className="flex flex-col gap-1">
                      <Switch checked={rules.online_ocr && Boolean(caps?.online_ocr)} disabled={!caps?.online_ocr}
                        onChange={(v) => patchRules({ online_ocr: v })}
                        label="Try the free online OCR first"
                        hint={caps?.online_ocr
                          ? 'The page picture is sent to the OCR service. If it is busy or out of reach, the offline engine takes over.'
                          : 'Not set up on this server, so the offline engine is used (free, unlimited, nothing leaves your network).'} />
                      <p className="ml-14 flex items-center gap-2 text-xs text-ink-faint">
                        {caps?.online_ocr ? <Globe className="h-3.5 w-3.5" /> : <Cpu className="h-3.5 w-3.5" />}
                        Order: PDF text → {caps?.online_ocr && rules.online_ocr ? 'online OCR → ' : ''}offline OCR{caps && !caps.offline_ocr ? ' (models missing!)' : ''}
                      </p>
                    </div>
                  )}
                </div>
                <div className="flex flex-col gap-1 pt-2">
                  <Switch checked={rules.skip_blank} onChange={(v) => patchRules({ skip_blank: v })} label="Skip blank pages" />
                  <Switch checked={rules.keep_duplicates} onChange={(v) => patchRules({ keep_duplicates: v })}
                    label="Keep a second page of the same invoice" hint="Off: only the first page of an invoice is kept; later ones show as duplicates." />
                </div>
                {docs.length > 1 && (
                  <div className="flex flex-col gap-2 pt-3">
                    <p className="text-sm font-medium text-ink">Output</p>
                    <Choice<'per_document' | 'combined'> label="Output" value={output} onChange={(v) => patch({ output: v })}
                      options={[
                        { value: 'per_document', title: 'One PDF per document', hint: 'Each uploaded PDF gets its own stamped PDF.' },
                        { value: 'combined', title: 'One combined PDF', hint: 'All kept pages together, in upload order.' },
                      ]} />
                  </div>
                )}
              </div>
            </Step>
          </div>

          <div className="flex min-w-0 flex-col gap-6 xl:sticky xl:top-20">
            {!filterMode && stampCard}
            {runCard}
          </div>
        </div>

        {jobId && (
          <GlassCard padding="lg" className="flex flex-col gap-4">
            <h3 className="font-display text-lg font-semibold text-ink">{result ? 'Finished' : 'Working…'}</h3>
            <ProgressPanel<RunResult>
              jobId={jobId}
              poller={pollRun}
              onDone={(next) => setResult(next ?? null)}
              onError={(message) => { setError(message); setJobId(null) }}
              onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
            />
          </GlassCard>
        )}
        <div ref={resultsRef}>{jobId && result && <ResultsPanel jobId={jobId} result={result} />}</div>
      </div>
    </AppShell>
  )
}
