import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Archive,
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  Download,
  Eye,
  FileText,
  ListChecks,
  Mail,
  Pencil,
  RefreshCw,
  RotateCcw,
  Settings2,
  Table2,
  Trash2,
  X,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { SearchBox } from '@/components/SearchBox'
import { Pagination } from '@/components/Pagination'
import { DatePicker, MonthYearPicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import {
  formatFilenameDate,
  formatIndianNumber,
  formatReportMonth,
  getIndianDateInputValue,
  getIndianMonthInputValue,
} from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/unaccounted'

const REPORT_LABELS: Record<string, string> = {
  unaccounted: 'Unaccounted Transactions',
  mrn: 'Pending MRN',
  po: 'Uninvoiced Expense PO',
}

type PeriodDetectionStatus = 'idle' | 'detecting' | 'complete' | 'failed'

function periodDetectionError(error: unknown, reportLabel: string): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error && error.message) return error.message
  return `Failed to detect periods in the ${reportLabel} file.`
}

function PeriodDetectionNotice({
  status,
  count,
  unit,
  error,
  onRetry,
}: {
  status: PeriodDetectionStatus
  count: number
  unit: 'period' | 'month'
  error?: string | null
  onRetry: () => void
}) {
  if (status === 'idle') return null

  if (status === 'detecting') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 rounded-xl border border-accent/25 bg-accent/[0.06] px-3 py-2.5 text-sm text-ink-dim"
      >
        <RefreshCw className="h-4 w-4 shrink-0 animate-spin text-accent motion-reduce:animate-none" />
        Detecting periods from the uploaded file… Processing will unlock when this is complete.
      </div>
    )
  }

  if (status === 'complete') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.07] px-3 py-2.5 text-sm text-emerald-700 dark:text-emerald-300"
      >
        <CheckCircle2 className="h-4 w-4 shrink-0" />
        {count} {count === 1 ? unit : `${unit}s`} detected. Period selection is ready.
      </div>
    )
  }

  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-xl border border-red-500/30 bg-red-500/[0.08] px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <span className="flex min-w-0 items-start gap-2 text-sm text-red-600 dark:text-red-300">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{error || 'Period detection failed. Retry before processing this report.'}</span>
      </span>
      <Button type="button" variant="secondary" onClick={onRetry} className="min-h-9 shrink-0 px-3 py-1.5 text-xs">
        Retry detection
      </Button>
    </div>
  )
}

// ── shared report-job types & log panel ──────────────────────────────────

interface ReportJobResult {
  total_rows: number
  input_cols: number
  matched: number
  unmatched: number
  unmapped_sites: string[]
  output_path: string
  log: [string, string][]
}

interface ReportJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: ReportJobResult | null
  error: string | null
}

async function pollReportJob(jobId: string): Promise<JobState<ReportJobResult>> {
  const job = await get<ReportJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function levelColor(level: string): string {
  switch (level.toLowerCase()) {
    case 'ok':
    case 'success':
      return 'text-emerald-400'
    case 'warn':
    case 'warning':
      return 'text-amber-400'
    case 'error':
      return 'text-red-400'
    default:
      return 'text-sky-400'
  }
}

function LogPanel({ log }: { log: [string, string][] }) {
  const pagination = usePagination(log ?? [], 10)
  if (!log || log.length === 0) return null
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-slate-950 p-3 font-mono text-xs leading-relaxed">
      <div className="flex flex-col gap-0.5">
        {pagination.pagedItems.map(([level, msg], index) => (
          <div key={`${pagination.startIndex + index}-${level}-${msg}`} className="flex gap-2">
            <span className={cn('shrink-0 font-semibold uppercase', levelColor(level))}>[{level}]</span>
            <span className="text-slate-300">{msg}</span>
          </div>
        ))}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        pageSizeOptions={[10, 25, 50]}
        itemLabel="log entries"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
        className="border-slate-700 bg-slate-900/80 font-sans"
      />
    </div>
  )
}

function ResultSummary({ result, onDownload }: { result: ReportJobResult; onDownload: () => void }) {
  return (
    <div className="subpanel flex flex-wrap items-center gap-6 p-4 text-sm">
      <div>
        <span className="text-ink-faint">Total rows</span>
        <p className="text-ink">{formatIndianNumber(result.total_rows)}</p>
      </div>
      <div>
        <span className="text-ink-faint">Matched</span>
        <p className="text-ink">{formatIndianNumber(result.matched)}</p>
      </div>
      <div>
        <span className="text-ink-faint">Unmatched</span>
        <p className="text-ink">{formatIndianNumber(result.unmatched)}</p>
      </div>
      <Button className="sm:ml-auto" icon={<Download className="h-4 w-4" />} onClick={onDownload}>
        Download
      </Button>
    </div>
  )
}

// ── missing-mapping fix flow (Location only; Incharge derived server-side) ──

function UnmappedSitesFix({
  sites,
  knownLocations,
  onRegenerate,
  regenerating,
}: {
  sites: string[]
  knownLocations: string[]
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [forms, setForms] = useState<Record<string, string>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(sites, 10)

  async function handleFix(site: string) {
    const location = forms[site]?.trim()
    if (!location) return
    setFixing(site)
    setError(null)
    try {
      await post(`${BASE}/mappings/fix`, { supplier_site: site, location })
      setFixed((prev) => ({ ...prev, [site]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = sites.length > 0 && sites.every((s) => fixed[s])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing mappings ({formatIndianNumber(sites.length)} supplier site{sites.length === 1 ? '' : 's'})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        Pick a Location for each unmapped Supplier Site. Accounts Incharge is filled in
        automatically from the Location table.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((site) => (
          <div
            key={site}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">{site}</span>
            <CreatableCombobox
              placeholder="Location"
              value={forms[site] ?? ''}
              options={knownLocations}
              disabled={fixed[site]}
              onChange={(value) => setForms((prev) => ({ ...prev, [site]: value }))}
              ariaLabel={`Location for supplier site ${site}`}
              suggestionLabel="Existing locations"
              className="w-full sm:w-64"
            />
            {fixed[site] ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === site}
                disabled={!forms[site]?.trim()}
                onClick={() => void handleFix(site)}
              >
                Fix
              </Button>
            )}
          </div>
        ))}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="unmapped sites"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
      <div className="flex justify-stretch sm:justify-end">
        <Button
          icon={<RefreshCw className="h-4 w-4" />}
          disabled={!allFixed}
          loading={regenerating}
          onClick={onRegenerate}
        >
          Regenerate report
        </Button>
      </div>
    </div>
  )
}

// ── Unaccounted Transactions tab ─────────────────────────────────────────

function UnaccountedTab({ knownLocations }: { knownLocations: string[] }) {
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ReportJobResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleRun() {
    if (files.length === 0) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const fd = new FormData()
      files.forEach((f) => fd.append('files', f))
      const res = await postForm<{ job_id: string }>(`${BASE}/unaccounted/process`, fd)
      setJobId(res.job_id)
      // submitting stays true until the background job itself settles
      // (cleared in ProgressPanel's onDone/onError below) - the request
      // returning just means the job was queued, not that it's finished.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start report generation.')
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = 'Unaccounted_Transactions.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <div className="flex flex-col gap-5">
      <FileDropzone
        multiple
        accept=".xls,.xlsx,.htm,.html"
        label="Drag & drop weekly export files here, or click to browse"
        hint="Multiple weekly exports are merged automatically"
        files={files}
        onFilesSelected={(f) => setFiles((prev) => [...prev, ...f])}
        onRemove={(i) => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
      />
      <div className="flex justify-stretch sm:justify-end">
        <Button onClick={() => void handleRun()} loading={submitting} disabled={files.length === 0}>
          Generate report
        </Button>
      </div>
      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}
      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={pollReportJob}
          onDone={(r) => {
            setResult(r ?? null)
            setSubmitting(false)
          }}
          onError={(e) => {
            setError(e)
            setSubmitting(false)
          }}
          onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
        />
      )}
      {result && (
        <div className="flex flex-col gap-4">
          <ResultSummary result={result} onDownload={handleDownload} />
          <LogPanel log={result.log} />
          {result.unmapped_sites.length > 0 && (
            <UnmappedSitesFix
              sites={result.unmapped_sites}
              knownLocations={knownLocations}
              regenerating={submitting}
              onRegenerate={() => void handleRun()}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Pending MRN tab ───────────────────────────────────────────────────────

function MrnTab({ knownLocations }: { knownLocations: string[] }) {
  const [file, setFile] = useState<File | null>(null)
  const [periods, setPeriods] = useState<string[]>([])
  const [includedPeriods, setIncludedPeriods] = useState<Record<string, boolean>>({})
  const [detectionStatus, setDetectionStatus] = useState<PeriodDetectionStatus>('idle')
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ReportJobResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const detectionRequest = useRef(0)
  const periodPagination = usePagination(periods, 10, file?.name)

  async function handleFileSelected(f: File[]) {
    const requestId = ++detectionRequest.current
    const picked = f[0] ?? null
    setFile(picked)
    setPeriods([])
    setIncludedPeriods({})
    setResult(null)
    setJobId(null)
    setError(null)
    if (!picked) {
      setDetectionStatus('idle')
      return
    }
    setDetectionStatus('detecting')
    try {
      const fd = new FormData()
      fd.append('file', picked)
      const res = await postForm<{ periods: string[] }>(`${BASE}/mrn/detect-periods`, fd)
      if (requestId !== detectionRequest.current) return
      if (res.periods.length === 0) {
        throw new Error('No periods were detected in the Pending MRN file.')
      }
      setPeriods(res.periods)
      setIncludedPeriods(Object.fromEntries(res.periods.map((p) => [p, true])))
      setDetectionStatus('complete')
    } catch (err) {
      if (requestId !== detectionRequest.current) return
      setDetectionStatus('failed')
      setError(periodDetectionError(err, 'Pending MRN'))
    }
  }

  async function handleRun() {
    if (!file || detectionStatus !== 'complete') {
      setError('Period detection must complete successfully before generating the Pending MRN report.')
      return
    }
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const excludePeriods = periods.filter((p) => !includedPeriods[p])
      const fd = new FormData()
      fd.append('file', file)
      fd.append('exclude_periods', excludePeriods.join(','))
      const res = await postForm<{ job_id: string }>(`${BASE}/mrn/process`, fd)
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start report generation.')
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = 'Pending_MRN.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <div className="flex flex-col gap-5">
      <FileDropzone
        accept=".xls,.xlsx,.htm,.html"
        label="Drag & drop the MRN export here, or click to browse"
        files={file ? [file] : []}
        onFilesSelected={(f) => void handleFileSelected(f)}
        onRemove={() => void handleFileSelected([])}
      />
      {file && (
        <PeriodDetectionNotice
          status={detectionStatus}
          count={periods.length}
          unit="period"
          error={detectionStatus === 'failed' ? error : null}
          onRetry={() => void handleFileSelected([file])}
        />
      )}
      {periods.length > 0 && (
        <div className="flex flex-col gap-2 rounded-xl border border-border p-4">
          <span className="text-sm font-medium text-ink-dim">
            Periods found. Uncheck any to exclude them from the report
          </span>
          <div className="flex flex-wrap gap-3">
            {periodPagination.pagedItems.map((p) => (
              <label key={p} className="inline-flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={includedPeriods[p] ?? true}
                  onChange={(e) => setIncludedPeriods((prev) => ({ ...prev, [p]: e.target.checked }))}
                  className="h-4 w-4 accent-accent"
                />
                {p}
              </label>
            ))}
          </div>
          <Pagination
            page={periodPagination.page}
            pageCount={periodPagination.pageCount}
            pageSize={periodPagination.pageSize}
            totalItems={periodPagination.totalItems}
            itemLabel="periods"
            onPageChange={periodPagination.setPage}
            onPageSizeChange={periodPagination.setPageSize}
          />
        </div>
      )}
      <div className="flex justify-stretch sm:justify-end">
        <Button
          onClick={() => void handleRun()}
          loading={submitting}
          disabled={!file || detectionStatus !== 'complete'}
        >
          Generate report
        </Button>
      </div>
      {error && detectionStatus !== 'failed' && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}
      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={pollReportJob}
          onDone={(r) => {
            setResult(r ?? null)
            setSubmitting(false)
          }}
          onError={(e) => {
            setError(e)
            setSubmitting(false)
          }}
          onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
        />
      )}
      {result && (
        <div className="flex flex-col gap-4">
          <ResultSummary result={result} onDownload={handleDownload} />
          <LogPanel log={result.log} />
          {result.unmapped_sites.length > 0 && (
            <UnmappedSitesFix
              sites={result.unmapped_sites}
              knownLocations={knownLocations}
              regenerating={submitting}
              onRegenerate={() => void handleRun()}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Uninvoiced Expense PO tab ─────────────────────────────────────────────

function PoTab({ knownLocations }: { knownLocations: string[] }) {
  const [file, setFile] = useState<File | null>(null)
  const [months, setMonths] = useState<string[]>([])
  const [includedMonths, setIncludedMonths] = useState<Record<string, boolean>>({})
  const [detectionStatus, setDetectionStatus] = useState<PeriodDetectionStatus>('idle')
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ReportJobResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const detectionRequest = useRef(0)
  const monthPagination = usePagination(months, 10, file?.name)

  async function handleFileSelected(f: File[]) {
    const requestId = ++detectionRequest.current
    const picked = f[0] ?? null
    setFile(picked)
    setMonths([])
    setIncludedMonths({})
    setResult(null)
    setJobId(null)
    setError(null)
    if (!picked) {
      setDetectionStatus('idle')
      return
    }
    setDetectionStatus('detecting')
    try {
      const fd = new FormData()
      fd.append('file', picked)
      const res = await postForm<{ periods: string[]; po_numbers: string[] }>(`${BASE}/po/detect-periods`, fd)
      if (requestId !== detectionRequest.current) return
      if (res.periods.length === 0) {
        throw new Error('No months were detected in the Uninvoiced Expense PO file.')
      }
      setMonths(res.periods)
      setIncludedMonths(Object.fromEntries(res.periods.map((p) => [p, true])))
      setDetectionStatus('complete')
    } catch (err) {
      if (requestId !== detectionRequest.current) return
      setDetectionStatus('failed')
      setError(periodDetectionError(err, 'Uninvoiced Expense PO'))
    }
  }

  async function handleRun() {
    if (!file || detectionStatus !== 'complete') {
      setError('Period detection must complete successfully before generating the Uninvoiced Expense PO report.')
      return
    }
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const excludeMonths = months.filter((m) => !includedMonths[m])
      const fd = new FormData()
      fd.append('file', file)
      fd.append('exclude_months', excludeMonths.join(','))
      // keywords/fuzzy_threshold deliberately omitted - backend falls back to
      // the saved PO Keywords mapping (see Mappings tab).
      const res = await postForm<{ job_id: string }>(`${BASE}/po/process`, fd)
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start report generation.')
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = 'Uninvoiced_Expense_PO.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <div className="flex flex-col gap-5">
      <FileDropzone
        accept=".xls,.xlsx,.htm,.html"
        label="Drag & drop the PO export here, or click to browse"
        files={file ? [file] : []}
        onFilesSelected={(f) => void handleFileSelected(f)}
        onRemove={() => void handleFileSelected([])}
      />
      {file && (
        <PeriodDetectionNotice
          status={detectionStatus}
          count={months.length}
          unit="month"
          error={detectionStatus === 'failed' ? error : null}
          onRetry={() => void handleFileSelected([file])}
        />
      )}
      {months.length > 0 && (
        <div className="flex flex-col gap-2 rounded-xl border border-border p-4">
          <span className="text-sm font-medium text-ink-dim">
            Months found. Uncheck any to exclude them from the report
          </span>
          <div className="flex flex-wrap gap-3">
            {monthPagination.pagedItems.map((m) => (
              <label key={m} className="inline-flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={includedMonths[m] ?? true}
                  onChange={(e) => setIncludedMonths((prev) => ({ ...prev, [m]: e.target.checked }))}
                  className="h-4 w-4 accent-accent"
                />
                {m}
              </label>
            ))}
          </div>
          <Pagination
            page={monthPagination.page}
            pageCount={monthPagination.pageCount}
            pageSize={monthPagination.pageSize}
            totalItems={monthPagination.totalItems}
            itemLabel="months"
            onPageChange={monthPagination.setPage}
            onPageSizeChange={monthPagination.setPageSize}
          />
        </div>
      )}

      <p className="text-xs text-ink-faint">
        Rows are filtered using the fuzzy-match keywords and threshold configured in the{' '}
        <span className="font-medium text-ink-dim">Mappings</span> tab.
      </p>

      <div className="flex justify-stretch sm:justify-end">
        <Button
          onClick={() => void handleRun()}
          loading={submitting}
          disabled={!file || detectionStatus !== 'complete'}
        >
          Generate report
        </Button>
      </div>
      {error && detectionStatus !== 'failed' && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}
      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={pollReportJob}
          onDone={(r) => {
            setResult(r ?? null)
            setSubmitting(false)
          }}
          onError={(e) => {
            setError(e)
            setSubmitting(false)
          }}
          onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
        />
      )}
      {result && (
        <div className="flex flex-col gap-4">
          <ResultSummary result={result} onDownload={handleDownload} />
          <LogPanel log={result.log} />
          {result.unmapped_sites.length > 0 && (
            <UnmappedSitesFix
              sites={result.unmapped_sites}
              knownLocations={knownLocations}
              regenerating={submitting}
              onRegenerate={() => void handleRun()}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Mail tab ──────────────────────────────────────────────────────────────

const LEGACY_MAIL_TO = ['accountsincharges@rdc.in', 'accountsgroup@rdc.in']
const LEGACY_MAIL_CC = ['manish.modani@rdc.in', 'umesh.gawade@rdc.in']

function getDefaultMailMonth() {
  return getIndianMonthInputValue()
}

function getDefaultAsOnDate() {
  return getIndianDateInputValue()
}

function joinMailLabels(items: string[]) {
  if (items.length === 0) return ''
  if (items.length === 1) return items[0]
  if (items.length === 2) return `${items[0]} and ${items[1]}`
  return `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`
}

function buildSuggestedMailSubject(
  month: string,
  includeUa: boolean,
  includeMrn: boolean,
  includePo: boolean,
) {
  const reports = [
    includeUa ? 'Unaccounted Transactions' : '',
    includeMrn ? 'Pending MRN' : '',
    includePo ? 'Uninvoiced Expense' : '',
  ].filter(Boolean)
  return `${joinMailLabels(reports) || 'Report'} till ${month}`
}

function buildSuggestedMailIntro(
  includeUa: boolean,
  includeMrn: boolean,
  includePo: boolean,
  monthUa: string,
  monthMrn: string,
  monthPo: string,
) {
  const reports = [
    includeUa ? `Unaccounted transactions till ${monthUa || 'Month'}` : '',
    includeMrn ? `Pending MRN till ${monthMrn || 'Month'}` : '',
    includePo ? `Uninvoiced Expenses till ${monthPo || 'Month'}` : '',
  ].filter(Boolean)
  const reportLines = reports.length === 1
    ? reports
    : reports.map((report, index) => `${index + 1}. ${report}`)

  return [
    'Dear Team,',
    '',
    'Please find below:',
    '',
    ...reportLines,
    '',
    'Kindly clear the same on priority basis and confirm.',
    '',
    '**Also note that Rent and Land Lease have been excluded from the Uninvoiced Expense Report. Accordingly, the remaining expenses are required to be booked.**',
  ].join('\n')
}

const DEFAULT_MAIL_MONTH = getDefaultMailMonth()
const DEFAULT_MAIL_MONTH_LABEL = formatReportMonth(DEFAULT_MAIL_MONTH)

const MAIL_MONTHS_STORAGE_KEY = 'accounts-suite-unaccounted-mail-months'

interface StoredMailMonths {
  subject?: string
  ua?: string
  mrn?: string
  po?: string
}

function loadStoredMailMonths(): StoredMailMonths {
  try {
    const raw = window.localStorage.getItem(MAIL_MONTHS_STORAGE_KEY)
    if (!raw) return {}
    return JSON.parse(raw) as StoredMailMonths
  } catch {
    return {}
  }
}

interface MailDefaultsResponse {
  to: string[]
  cc: string[]
  default_month: string
  subject: string
  intro: string
  include_ua: boolean
  include_mrn: boolean
  include_po: boolean
}

interface MailJobResult {
  status: 'preview' | 'needs_mapping_fix' | 'email_not_configured'
  message?: string
  subject?: string
  html_body?: string
  from_email?: string
  to?: string[]
  cc?: string[]
  attachments?: string[]
  unmapped_sites?: Record<string, string[]>
  output_paths?: Record<string, string | null>
  log: [string, string][]
}

interface MailJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: MailJobResult | null
  error: string | null
}

async function pollMailJob(jobId: string): Promise<JobState<MailJobResult>> {
  const job = await get<MailJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function MailUnmappedGroup({
  reportKey,
  label,
  sites,
  knownLocations,
  fixedKeys,
  onFixed,
}: {
  reportKey: string
  label: string
  sites: string[]
  knownLocations: string[]
  fixedKeys: Record<string, boolean>
  onFixed: (reportKey: string, site: string, location: string) => Promise<void>
}) {
  const [forms, setForms] = useState<Record<string, string>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(sites, 10)

  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm font-semibold text-ink">{label}</span>
      {error && <p className="text-sm text-red-500">{error}</p>}
      {pagination.pagedItems.map((site) => {
        const fixKey = `${reportKey}:${site}`
        const fixed = Boolean(fixedKeys[fixKey])
        return (
          <div
            key={site}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">{site}</span>
            <CreatableCombobox
              placeholder="Location"
              value={forms[site] ?? ''}
              options={knownLocations}
              disabled={fixed}
              onChange={(value) => setForms((prev) => ({ ...prev, [site]: value }))}
              ariaLabel={`Location for supplier site ${site}`}
              suggestionLabel="Existing locations"
              className="w-full sm:w-64"
            />
            {fixed ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === site}
                disabled={!forms[site]?.trim()}
                onClick={async () => {
                  setFixing(site)
                  setError(null)
                  try {
                    await onFixed(reportKey, site, forms[site].trim())
                  } catch (err) {
                    setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
                  } finally {
                    setFixing(null)
                  }
                }}
              >
                Fix
              </Button>
            )}
          </div>
        )
      })}
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="unmapped sites"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

function MailTab({
  knownLocations,
  onManageMappings,
}: {
  knownLocations: string[]
  onManageMappings: () => void
}) {
  const [includeUa, setIncludeUa] = useState(true)
  const [includeMrn, setIncludeMrn] = useState(true)
  const [includePo, setIncludePo] = useState(true)

  const [uaFiles, setUaFiles] = useState<File[]>([])
  const [mrnFile, setMrnFile] = useState<File | null>(null)
  const [poFile, setPoFile] = useState<File | null>(null)

  const [mrnPeriods, setMrnPeriods] = useState<string[]>([])
  const [mrnIncluded, setMrnIncluded] = useState<Record<string, boolean>>({})
  const [poMonths, setPoMonths] = useState<string[]>([])
  const [poIncluded, setPoIncluded] = useState<Record<string, boolean>>({})

  const [monthSubject, setMonthSubject] = useState(() => loadStoredMailMonths().subject || DEFAULT_MAIL_MONTH)
  const [monthUa, setMonthUa] = useState(() => loadStoredMailMonths().ua || DEFAULT_MAIL_MONTH)
  const [monthMrn, setMonthMrn] = useState(() => loadStoredMailMonths().mrn || DEFAULT_MAIL_MONTH)
  const [monthPo, setMonthPo] = useState(() => loadStoredMailMonths().po || DEFAULT_MAIL_MONTH)
  const [asOnDate, setAsOnDate] = useState(getDefaultAsOnDate)

  useEffect(() => {
    try {
      window.localStorage.setItem(
        MAIL_MONTHS_STORAGE_KEY,
        JSON.stringify({ subject: monthSubject, ua: monthUa, mrn: monthMrn, po: monthPo }),
      )
    } catch {
      // Storage can be disabled by browser privacy settings; the pickers still work for this page.
    }
  }, [monthSubject, monthUa, monthMrn, monthPo])
  const [customSubject, setCustomSubject] = useState(() =>
    buildSuggestedMailSubject(DEFAULT_MAIL_MONTH_LABEL, true, true, true),
  )
  const [customIntro, setCustomIntro] = useState(() =>
    buildSuggestedMailIntro(
      true,
      true,
      true,
      DEFAULT_MAIL_MONTH_LABEL,
      DEFAULT_MAIL_MONTH_LABEL,
      DEFAULT_MAIL_MONTH_LABEL,
    ),
  )
  const [subjectCustomized, setSubjectCustomized] = useState(false)
  const [introCustomized, setIntroCustomized] = useState(false)
  const [to, setTo] = useState(LEGACY_MAIL_TO.join(', '))
  const [cc, setCc] = useState(LEGACY_MAIL_CC.join(', '))

  const [mrnDetectionStatus, setMrnDetectionStatus] = useState<PeriodDetectionStatus>('idle')
  const [poDetectionStatus, setPoDetectionStatus] = useState<PeriodDetectionStatus>('idle')
  const [mrnDetectionError, setMrnDetectionError] = useState<string | null>(null)
  const [poDetectionError, setPoDetectionError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobResult, setJobResult] = useState<MailJobResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notConfigured, setNotConfigured] = useState(false)
  const [fixedSites, setFixedSites] = useState<Record<string, boolean>>({})
  const [confirmSending, setConfirmSending] = useState(false)
  const [sentResult, setSentResult] = useState<{ subject: string; to: string[]; cc: string[] } | null>(
    null,
  )
  const mrnDetectionRequest = useRef(0)
  const poDetectionRequest = useRef(0)
  const mrnPeriodPagination = usePagination(mrnPeriods, 10, mrnFile?.name)
  const poMonthPagination = usePagination(poMonths, 10, poFile?.name)
  const suggestedSubject = buildSuggestedMailSubject(
    formatReportMonth(monthSubject) || 'Mon-YY',
    includeUa,
    includeMrn,
    includePo,
  )
  const suggestedIntro = buildSuggestedMailIntro(
    includeUa,
    includeMrn,
    includePo,
    formatReportMonth(monthUa),
    formatReportMonth(monthMrn),
    formatReportMonth(monthPo),
  )

  useEffect(() => {
    void get<MailDefaultsResponse>(`${BASE}/mail/defaults`)
      .then((res) => {
        setTo(res.to.join(', '))
        setCc(res.cc.join(', '))
      })
      .catch(() => {
        // Keep the original desktop defaults when centralized settings cannot load.
      })
  }, [])

  useEffect(() => {
    if (!subjectCustomized) setCustomSubject(suggestedSubject)
  }, [subjectCustomized, suggestedSubject])

  useEffect(() => {
    if (!introCustomized) setCustomIntro(suggestedIntro)
  }, [introCustomized, suggestedIntro])

  async function handleMrnFileSelected(f: File[]) {
    const requestId = ++mrnDetectionRequest.current
    const picked = f[0] ?? null
    setMrnFile(picked)
    setMrnPeriods([])
    setMrnIncluded({})
    setMrnDetectionError(null)
    setJobId(null)
    setJobResult(null)
    setSentResult(null)
    if (!picked) {
      setMrnDetectionStatus('idle')
      return
    }
    setMrnDetectionStatus('detecting')
    try {
      const fd = new FormData()
      fd.append('file', picked)
      const res = await postForm<{ periods: string[] }>(`${BASE}/mrn/detect-periods`, fd)
      if (requestId !== mrnDetectionRequest.current) return
      if (res.periods.length === 0) {
        throw new Error('No periods were detected in the Pending MRN file.')
      }
      setMrnPeriods(res.periods)
      setMrnIncluded(Object.fromEntries(res.periods.map((p) => [p, true])))
      setMrnDetectionStatus('complete')
    } catch (err) {
      if (requestId !== mrnDetectionRequest.current) return
      setMrnDetectionStatus('failed')
      setMrnDetectionError(periodDetectionError(err, 'Pending MRN'))
    }
  }

  async function handlePoFileSelected(f: File[]) {
    const requestId = ++poDetectionRequest.current
    const picked = f[0] ?? null
    setPoFile(picked)
    setPoMonths([])
    setPoIncluded({})
    setPoDetectionError(null)
    setJobId(null)
    setJobResult(null)
    setSentResult(null)
    if (!picked) {
      setPoDetectionStatus('idle')
      return
    }
    setPoDetectionStatus('detecting')
    try {
      const fd = new FormData()
      fd.append('file', picked)
      const res = await postForm<{ periods: string[] }>(`${BASE}/po/detect-periods`, fd)
      if (requestId !== poDetectionRequest.current) return
      if (res.periods.length === 0) {
        throw new Error('No months were detected in the Uninvoiced Expense PO file.')
      }
      setPoMonths(res.periods)
      setPoIncluded(Object.fromEntries(res.periods.map((p) => [p, true])))
      setPoDetectionStatus('complete')
    } catch (err) {
      if (requestId !== poDetectionRequest.current) return
      setPoDetectionStatus('failed')
      setPoDetectionError(periodDetectionError(err, 'Uninvoiced Expense PO'))
    }
  }

  async function handleSend(forceSend: boolean) {
    if (!canPrepareReports) {
      setError(
        'Upload every selected report file and wait for Pending MRN and Uninvoiced Expense PO period detection to finish.',
      )
      return
    }
    setSubmitting(true)
    setError(null)
    setNotConfigured(false)
    setSentResult(null)
    if (!forceSend) {
      setJobId(null)
      setJobResult(null)
    }
    try {
      const fd = new FormData()
      uaFiles.forEach((f) => fd.append('ua_files', f))
      if (mrnFile) fd.append('mrn_file', mrnFile)
      if (poFile) fd.append('po_file', poFile)
      fd.append('exclude_periods', mrnPeriods.filter((p) => !mrnIncluded[p]).join(','))
      fd.append('exclude_months', poMonths.filter((m) => !poIncluded[m]).join(','))
      // keywords/fuzzy_threshold deliberately omitted - the backend falls back
      // to the saved PO Keywords mapping (Mappings tab) when not provided,
      // matching the desktop app: fuzzy-match config lives only in the
      // mapping editor, never edited ad-hoc alongside a single send.
      fd.append('month_subject', formatReportMonth(monthSubject))
      fd.append('month_ua', formatReportMonth(monthUa))
      fd.append('month_mrn', formatReportMonth(monthMrn))
      fd.append('month_po', formatReportMonth(monthPo))
      fd.append('as_on_date', formatFilenameDate(asOnDate))
      fd.append('include_ua', String(includeUa))
      fd.append('include_mrn', String(includeMrn))
      fd.append('include_po', String(includePo))
      fd.append('custom_subject', customSubject)
      fd.append('custom_intro', customIntro)
      fd.append('to', to)
      fd.append('cc', cc)
      fd.append('force_send', String(forceSend))
      const res = await postForm<{ job_id: string }>(`${BASE}/mail/send`, fd)
      setJobId(res.job_id)
      // submitting stays true until the job itself settles (see
      // ProgressPanel's onDone/onError below) - the request returning just
      // means report generation + preview building was queued.
    } catch (err) {
      if (err instanceof ApiError && err.status === 400 && /Settings/i.test(err.message)) {
        setNotConfigured(true)
      } else {
        setError(err instanceof ApiError ? err.message : 'Failed to send.')
      }
      setSubmitting(false)
    }
  }

  async function handleFixSite(reportKey: string, site: string, location: string) {
    await post(`${BASE}/mappings/fix`, { supplier_site: site, location })
    setFixedSites((prev) => ({ ...prev, [`${reportKey}:${site}`]: true }))
  }

  async function handleConfirmSend() {
    if (!jobId) return
    setConfirmSending(true)
    setError(null)
    try {
      const res = await post<{ status: string; subject: string; to: string[]; cc: string[] }>(
        `${BASE}/mail/confirm-send`,
        { job_id: jobId },
        5 * 60_000, // SMTP dispatch with attachments can run well past the default 30s
      )
      setSentResult({ subject: res.subject, to: res.to, cc: res.cc })
      setJobResult(null)
      setJobId(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to send.')
    } finally {
      setConfirmSending(false)
    }
  }

  function handleClearAll() {
    mrnDetectionRequest.current += 1
    poDetectionRequest.current += 1
    setUaFiles([])
    setMrnFile(null)
    setPoFile(null)
    setMrnPeriods([])
    setMrnIncluded({})
    setPoMonths([])
    setPoIncluded({})
    setMrnDetectionStatus('idle')
    setPoDetectionStatus('idle')
    setMrnDetectionError(null)
    setPoDetectionError(null)
    setAsOnDate(getDefaultAsOnDate())
    setJobId(null)
    setJobResult(null)
    setSentResult(null)
    setFixedSites({})
    setError(null)
    setNotConfigured(false)
  }

  function handleDownloadMailReport(reportKey: string) {
    if (!jobId) return
    const anchor = document.createElement('a')
    anchor.href = apiUrl(`${BASE}/mail/download/${jobId}/${reportKey}`)
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }

  const hasSelectedReport = includeUa || includeMrn || includePo
  const selectedFilesReady =
    (!includeUa || uaFiles.length > 0) &&
    (!includeMrn || Boolean(mrnFile)) &&
    (!includePo || Boolean(poFile))
  const requiredDetectionReady =
    (!includeMrn || mrnDetectionStatus === 'complete') &&
    (!includePo || poDetectionStatus === 'complete')
  const canPrepareReports = hasSelectedReport && selectedFilesReady && requiredDetectionReady
  const canSend = canPrepareReports && !submitting && !confirmSending

  const unmappedGroups = jobResult?.status === 'needs_mapping_fix' ? jobResult.unmapped_sites ?? {} : {}
  const allGroupsFixed = Object.entries(unmappedGroups).every(([key, sites]) =>
    sites.every((s) => fixedSites[`${key}:${s}`]),
  )

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-dim">
          Generates the selected reports and, once you've reviewed exactly what will go out, sends
          them from your own Settings sender identity.
        </p>
        <Button
          type="button"
          variant="secondary"
          icon={<Table2 className="h-4 w-4" />}
          onClick={onManageMappings}
        >
          Manage mappings
        </Button>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
        <span className="flex items-center gap-2 text-sm font-medium text-ink-dim">
          <Settings2 className="h-4 w-4" /> Reports to include
        </span>
        <div className="flex flex-wrap gap-5">
          <label className="inline-flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={includeUa}
              onChange={(e) => setIncludeUa(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Unaccounted Transactions
          </label>
          <label className="inline-flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={includeMrn}
              onChange={(e) => setIncludeMrn(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Pending MRN
          </label>
          <label className="inline-flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={includePo}
              onChange={(e) => setIncludePo(e.target.checked)}
              className="h-4 w-4 accent-accent"
            />
            Uninvoiced Expense PO
          </label>
        </div>
      </div>

      {includeUa && (
        <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            <FileText className="h-4 w-4 text-accent" /> Unaccounted Transactions
          </span>
          <FileDropzone
            multiple
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop weekly export files, or click to browse"
            hint="Multiple weekly exports are merged automatically"
            files={uaFiles}
            onFilesSelected={(f) => setUaFiles((prev) => [...prev, ...f])}
            onRemove={(i) => setUaFiles((prev) => prev.filter((_, idx) => idx !== i))}
          />
        </div>
      )}

      {includeMrn && (
        <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            <FileText className="h-4 w-4 text-accent" /> Pending MRN
          </span>
          <FileDropzone
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop the MRN export, or click to browse"
            files={mrnFile ? [mrnFile] : []}
            onFilesSelected={(f) => void handleMrnFileSelected(f)}
            onRemove={() => void handleMrnFileSelected([])}
          />
          {mrnFile && (
            <PeriodDetectionNotice
              status={mrnDetectionStatus}
              count={mrnPeriods.length}
              unit="period"
              error={mrnDetectionError}
              onRetry={() => void handleMrnFileSelected([mrnFile])}
            />
          )}
          {mrnPeriods.length > 0 && (
            <div className="flex flex-col gap-3 rounded-xl border border-border p-3">
              <div className="flex flex-wrap gap-3">
                {mrnPeriodPagination.pagedItems.map((p) => (
                  <label key={p} className="inline-flex items-center gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={mrnIncluded[p] ?? true}
                      onChange={(e) => setMrnIncluded((prev) => ({ ...prev, [p]: e.target.checked }))}
                      className="h-4 w-4 accent-accent"
                    />
                    {p}
                  </label>
                ))}
              </div>
              <Pagination
                page={mrnPeriodPagination.page}
                pageCount={mrnPeriodPagination.pageCount}
                pageSize={mrnPeriodPagination.pageSize}
                totalItems={mrnPeriodPagination.totalItems}
                itemLabel="periods"
                onPageChange={mrnPeriodPagination.setPage}
                onPageSizeChange={mrnPeriodPagination.setPageSize}
              />
            </div>
          )}
        </div>
      )}

      {includePo && (
        <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            <FileText className="h-4 w-4 text-accent" /> Uninvoiced Expense PO
          </span>
          <FileDropzone
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop the PO export, or click to browse"
            files={poFile ? [poFile] : []}
            onFilesSelected={(f) => void handlePoFileSelected(f)}
            onRemove={() => void handlePoFileSelected([])}
          />
          {poFile && (
            <PeriodDetectionNotice
              status={poDetectionStatus}
              count={poMonths.length}
              unit="month"
              error={poDetectionError}
              onRetry={() => void handlePoFileSelected([poFile])}
            />
          )}
          {poMonths.length > 0 && (
            <div className="flex flex-col gap-3 rounded-xl border border-border p-3">
              <div className="flex flex-wrap gap-3">
                {poMonthPagination.pagedItems.map((m) => (
                  <label key={m} className="inline-flex items-center gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={poIncluded[m] ?? true}
                      onChange={(e) => setPoIncluded((prev) => ({ ...prev, [m]: e.target.checked }))}
                      className="h-4 w-4 accent-accent"
                    />
                    {m}
                  </label>
                ))}
              </div>
              <Pagination
                page={poMonthPagination.page}
                pageCount={poMonthPagination.pageCount}
                pageSize={poMonthPagination.pageSize}
                totalItems={poMonthPagination.totalItems}
                itemLabel="months"
                onPageChange={poMonthPagination.setPage}
                onPageSizeChange={poMonthPagination.setPageSize}
              />
            </div>
          )}
          <p className="text-xs text-ink-faint">
            Rows are filtered using the fuzzy-match keywords and threshold configured in the{' '}
            <span className="font-medium text-ink-dim">Mappings</span> tab.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-4 rounded-xl border border-border p-4">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <CalendarDays className="h-4 w-4 text-accent" /> Period labels &amp; filename date
        </span>
        <p className="-mt-2 text-xs text-ink-faint">
          Every month used anywhere in this send, in one place. Set the subject month once and sync it
          everywhere, or fine-tune each report individually.
        </p>

        <div className="rounded-xl border border-border bg-bg-soft/35 p-3">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Subject month and year</span>
            <div className="flex flex-col gap-2 sm:flex-row">
              <MonthYearPicker
                value={monthSubject}
                onValueChange={setMonthSubject}
                aria-label="Subject month and year"
                className="flex-1"
              />
              <button
                type="button"
                onClick={() => {
                  setMonthUa(monthSubject)
                  setMonthMrn(monthSubject)
                  setMonthPo(monthSubject)
                }}
                className="min-h-11 rounded-xl border border-border bg-surface/65 px-4 text-xs font-semibold text-ink-dim transition hover:border-accent/35 hover:text-accent focus-visible:outline-2 focus-visible:outline-accent"
              >
                Sync all report months
              </button>
            </div>
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {includeUa && (
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Unaccounted: body line month</span>
              <MonthYearPicker
                value={monthUa}
                onValueChange={setMonthUa}
                aria-label="Unaccounted Transactions reporting month and year"
                className="text-sm"
              />
            </label>
          )}
          {includeMrn && (
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Pending MRN: body line month</span>
              <MonthYearPicker
                value={monthMrn}
                onValueChange={setMonthMrn}
                aria-label="Pending MRN reporting month and year"
                className="text-sm"
              />
            </label>
          )}
          {includePo && (
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">Uninvoiced Expenses: body line month</span>
              <MonthYearPicker
                value={monthPo}
                onValueChange={setMonthPo}
                aria-label="Uninvoiced Expense PO reporting month and year"
                className="text-sm"
              />
            </label>
          )}
        </div>

        <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:items-end">
          <label className="flex flex-1 flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">As-on date used in output filenames</span>
            <DatePicker
              value={asOnDate}
              onValueChange={setAsOnDate}
              aria-label="As-on date used in output filenames"
            />
          </label>
          <p className="pb-3 text-xs leading-5 text-ink-faint">
            Each generated workbook can be saved from the review step before the email is sent.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-4 rounded-xl border border-border p-4">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Mail className="h-4 w-4 text-accent" /> Message
        </span>
        <p className="-mt-2 text-xs text-ink-faint">
          Everything is prefilled from the original application. Recipients start with the
          admin-managed defaults, while the subject and body follow the reports selected above.
          Every field remains editable for this send.
        </p>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">To</span>
            <input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              inputMode="email"
              placeholder="accountsincharges@rdc.in, accountsgroup@rdc.in"
              className="field-control"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-ink-dim">Cc</span>
            <input
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              inputMode="email"
              placeholder="manish.modani@rdc.in, umesh.gawade@rdc.in"
              className="field-control"
            />
          </label>
        </div>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="flex flex-wrap items-center justify-between gap-2 font-medium text-ink-dim">
            <span>Email subject</span>
            <button
              type="button"
              disabled={!subjectCustomized}
              onClick={() => {
                setCustomSubject(suggestedSubject)
                setSubjectCustomized(false)
              }}
              className="text-xs font-semibold text-accent transition hover:text-accent-2 disabled:cursor-default disabled:text-ink-faint"
            >
              {subjectCustomized ? 'Use suggested subject' : 'Updates with selection'}
            </button>
          </span>
          <input
            value={customSubject}
            onChange={(e) => {
              setCustomSubject(e.target.value)
              setSubjectCustomized(e.target.value !== suggestedSubject)
            }}
            className="field-control"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="flex flex-wrap items-center justify-between gap-2 font-medium text-ink-dim">
            <span>Email body</span>
            <button
              type="button"
              disabled={!introCustomized}
              onClick={() => {
                setCustomIntro(suggestedIntro)
                setIntroCustomized(false)
              }}
              className="text-xs font-semibold text-accent transition hover:text-accent-2 disabled:cursor-default disabled:text-ink-faint"
            >
              {introCustomized ? 'Use suggested body' : 'Updates with selection'}
            </button>
          </span>
          <textarea
            value={customIntro}
            onChange={(e) => {
              setCustomIntro(e.target.value)
              setIntroCustomized(e.target.value !== suggestedIntro)
            }}
            rows={9}
            className="field-control resize-y leading-6"
          />
          <span className="text-xs leading-5 text-ink-faint">
            One report uses a single line; two or three reports are numbered automatically. Text
            wrapped in **double asterisks** appears bold in the email.
          </span>
        </label>
      </div>

      <p className="text-right text-xs text-ink-faint sm:text-left">
        This always generates the reports and shows you the exact email first. Nothing is ever
        sent without your explicit confirmation on the next screen.
      </p>
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button
          variant="secondary"
          icon={<Trash2 className="h-4 w-4" />}
          onClick={handleClearAll}
          disabled={submitting || confirmSending}
        >
          Clear all
        </Button>
        <Button
          icon={<Eye className="h-4 w-4" />}
          onClick={() => void handleSend(false)}
          loading={submitting}
          disabled={!canSend}
        >
          {submitting ? 'Preparing…' : 'Generate & Review Email'}
        </Button>
      </div>

      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}

      {notConfigured && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="flex flex-col gap-2 text-sm">
            <span className="text-ink-dim">You haven't set up your email sender yet. Go to Settings.</span>
            <Link
              to="/settings"
              className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent transition hover:gap-2.5"
            >
              Go to Settings <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      )}

      {jobId && (
        <ProgressPanel
          jobId={jobId}
          poller={pollMailJob}
          cancelOnTabClose={false}
          onDone={(r) => {
            setJobResult(r ?? null)
            setSubmitting(false)
          }}
          onError={(e) => {
            setError(e)
            setSubmitting(false)
          }}
          onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
        />
      )}

      {sentResult && (
        <div className="flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
          <div className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-emerald-600">Email sent successfully.</span>
            <span className="text-ink-dim">Subject: {sentResult.subject}</span>
            <span className="text-ink-dim">To: {sentResult.to.join(', ') || 'Not available'}</span>
            {sentResult.cc.length > 0 && <span className="text-ink-dim">Cc: {sentResult.cc.join(', ')}</span>}
          </div>
        </div>
      )}

      {jobResult && (
        <div className="flex flex-col gap-4">
          <LogPanel log={jobResult.log} />

          {jobResult.status === 'preview' && (
            <div className="flex flex-col gap-4 rounded-xl border border-accent/30 bg-accent/5 p-4">
              <div className="flex items-center gap-2 text-accent">
                <Eye className="h-4 w-4" />
                <h4 className="font-display text-sm font-semibold">
                  Review before sending. Nothing has been sent yet
                </h4>
              </div>

              <div className="grid grid-cols-1 gap-x-6 gap-y-2 rounded-xl border border-border bg-surface/60 p-4 text-sm sm:grid-cols-[auto_1fr]">
                <span className="text-ink-faint">From</span>
                <span className="text-ink">{jobResult.from_email}</span>
                <span className="text-ink-faint">To</span>
                <span className="text-ink">{(jobResult.to ?? []).join(', ') || 'Not available'}</span>
                {jobResult.cc && jobResult.cc.length > 0 && (
                  <>
                    <span className="text-ink-faint">Cc</span>
                    <span className="text-ink">{jobResult.cc.join(', ')}</span>
                  </>
                )}
                <span className="text-ink-faint">Subject</span>
                <span className="text-ink">{jobResult.subject}</span>
                <span className="text-ink-faint">Attachments</span>
                <span className="text-ink">
                  {(jobResult.attachments ?? []).map((a) => a.split(/[\\/]/).pop()).join(', ') || 'Not available'}
                </span>
              </div>

              {Object.entries(jobResult.output_paths ?? {}).some(([, path]) => Boolean(path)) && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface/60 p-3">
                  <span className="mr-auto text-sm font-medium text-ink-dim">Save generated workbooks</span>
                  {Object.entries(jobResult.output_paths ?? {}).map(([key, path]) => path && (
                    <Button
                      key={key}
                      variant="secondary"
                      icon={<Download className="h-4 w-4" />}
                      onClick={() => handleDownloadMailReport(key)}
                    >
                      {REPORT_LABELS[key] ?? key}
                    </Button>
                  ))}
                </div>
              )}

              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink-dim">Body preview</span>
                <iframe
                  title="Email body preview"
                  srcDoc={jobResult.html_body ?? ''}
                  sandbox=""
                  className="h-96 w-full rounded-xl border border-border bg-white"
                />
              </div>

              {error && <p className="text-sm text-red-500">{error}</p>}

              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setJobResult(null)
                    setJobId(null)
                  }}
                >
                  Cancel
                </Button>
                <Button
                  icon={<Mail className="h-4 w-4" />}
                  loading={confirmSending}
                  onClick={() => void handleConfirmSend()}
                >
                  Confirm & send
                </Button>
              </div>
            </div>
          )}

          {jobResult.status === 'email_not_configured' && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="flex flex-col gap-2 text-sm">
                <span className="text-ink-dim">
                  {jobResult.message ?? "You haven't set up your email sender yet. Go to Settings."}
                </span>
                <Link
                  to="/settings"
                  className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-accent transition hover:gap-2.5"
                >
                  Go to Settings <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            </div>
          )}

          {jobResult.status === 'needs_mapping_fix' && (
            <div className="flex flex-col gap-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
              <div className="flex items-center gap-2 text-amber-600">
                <AlertTriangle className="h-4 w-4" />
                <h4 className="font-display text-sm font-semibold">Missing mappings found</h4>
              </div>
              <p className="text-sm text-ink-dim">
                Fix each unmapped supplier site below, then resend without re-uploading files.
              </p>
              {Object.entries(unmappedGroups).map(([key, sites]) => (
                <MailUnmappedGroup
                  key={key}
                  reportKey={key}
                  label={REPORT_LABELS[key] ?? key}
                  sites={sites}
                  knownLocations={knownLocations}
                  fixedKeys={fixedSites}
                  onFixed={handleFixSite}
                />
              ))}
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button
                  variant="secondary"
                  loading={submitting}
                  onClick={() => void handleSend(true)}
                >
                  Preview with unmapped rows
                </Button>
                <Button
                  icon={<RefreshCw className="h-4 w-4" />}
                  disabled={!allGroupsFixed}
                  loading={submitting}
                  onClick={() => void handleSend(false)}
                >
                  Regenerate after fixes
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Mappings tab ──────────────────────────────────────────────────────────

interface UnaccountedMappingConfig {
  key: string
  title: string
  addLabel: string
  columns: MappingColumn[]
  idField: string
  buildBody: (row: MappingRow) => Record<string, unknown>
}

const UNACCOUNTED_MAPPING_CONFIGS: UnaccountedMappingConfig[] = [
  {
    key: 'location-incharge',
    title: 'Location → Incharge',
    addLabel: 'Add location incharge',
    idField: 'location',
    columns: [
      { key: 'location', label: 'Location' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildBody: (row) => ({
      location: row.location ?? '',
      accounts_incharge: row.accounts_incharge ?? '',
    }),
  },
  {
    key: 'site-overrides',
    title: 'Supplier Site Overrides',
    addLabel: 'Add site override',
    idField: 'supplier_site',
    columns: [
      { key: 'supplier_site', label: 'Supplier Site' },
      { key: 'location', label: 'Location' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildBody: (row) => ({
      supplier_site: row.supplier_site ?? '',
      location: row.location ?? '',
      accounts_incharge: row.accounts_incharge ?? '',
    }),
  },
  {
    key: 'creator',
    title: 'Created-By Mapping',
    addLabel: 'Add creator mapping',
    idField: 'created_by',
    columns: [
      { key: 'created_by', label: 'Created By' },
      { key: 'location', label: 'Location' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildBody: (row) => ({
      created_by: row.created_by ?? '',
      location: row.location ?? '',
      accounts_incharge: row.accounts_incharge ?? '',
    }),
  },
]

function UnaccountedMappingSection({ config }: { config: UnaccountedMappingConfig }) {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [archivedRows, setArchivedRows] = useState<MappingRow[]>([])
  const [archivedLoading, setArchivedLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get<MappingRow[]>(`${BASE}/mappings/${config.key}`)
      setRows(data)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load mapping table.')
    } finally {
      setLoading(false)
    }
  }, [config.key])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd(row: MappingRow) {
    await post(`${BASE}/mappings/${config.key}`, config.buildBody(row))
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    const original = rows[index]
    const key = encodeURIComponent(original[config.idField] ?? '')
    await put(`${BASE}/mappings/${config.key}/${key}`, config.buildBody(row))
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    const key = encodeURIComponent(original[config.idField] ?? '')
    await del(`${BASE}/mappings/${config.key}/${key}`)
    await load()
  }

  async function loadArchived() {
    setArchivedLoading(true)
    try {
      const data = await get<MappingRow[]>(`${BASE}/mappings/${config.key}/archived`)
      setArchivedRows(data)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load archived rows.')
    } finally {
      setArchivedLoading(false)
    }
  }

  async function handleRestore(index: number) {
    const original = archivedRows[index]
    const key = encodeURIComponent(original[config.idField] ?? '')
    await post(`${BASE}/mappings/${config.key}/${key}/restore`)
    await Promise.all([loadArchived(), load()])
  }

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
          {error}
        </p>
      )}
      {loading ? (
        <p className="py-10 text-center text-sm text-ink-faint">Loading...</p>
      ) : (
        <MappingTable
          title={config.title}
          addLabel={config.addLabel}
          columns={config.columns}
          rows={rows}
          onAdd={handleAdd}
          onEdit={handleEdit}
          onDelete={handleDelete}
          archive={{
            rows: archivedRows,
            loading: archivedLoading,
            onOpen: loadArchived,
            onRestore: handleRestore,
          }}
        />
      )}
    </div>
  )
}

function PoKeywordsPanel() {
  const [keywords, setKeywords] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [threshold, setThreshold] = useState(0.82)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [editingKeyword, setEditingKeyword] = useState<string | null>(null)
  const [editingValue, setEditingValue] = useState('')
  const filteredKeywords = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return query ? keywords.filter((keyword) => keyword.toLocaleLowerCase().includes(query)) : keywords
  }, [keywords, search])
  const pagination = usePagination(filteredKeywords, 10, search)

  useEffect(() => {
    void (async () => {
      setLoading(true)
      try {
        const res = await get<{ keywords: string[]; threshold: number }>(`${BASE}/po/keywords`)
        setKeywords(res.keywords)
        setThreshold(res.threshold)
      } catch {
        // ignore
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  function addKeyword() {
    const v = keywordInput.trim().toLowerCase()
    if (v && !keywords.includes(v)) setKeywords((prev) => [...prev, v])
    setKeywordInput('')
  }

  function commitKeywordEdit(oldKeyword: string) {
    const replacement = editingValue.trim().toLowerCase()
    if (!replacement || (replacement !== oldKeyword && keywords.includes(replacement))) {
      setMessage({ ok: false, text: replacement ? 'That keyword already exists.' : 'Keyword cannot be blank.' })
      return
    }
    setKeywords((previous) => previous.map((keyword) => keyword === oldKeyword ? replacement : keyword))
    setEditingKeyword(null)
    setEditingValue('')
  }

  async function handleResetKeywords() {
    const defaults = ['land rent', 'room rent', 'guest', 'ground rent']
    setSaving(true)
    setMessage(null)
    try {
      const response = await put<{ keywords: string[]; threshold: number }>(`${BASE}/po/keywords`, {
        keywords: defaults,
        threshold,
      })
      setKeywords(response.keywords)
      setThreshold(response.threshold)
      setMessage({ ok: true, text: 'Default keywords restored.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Failed to reset.' })
    } finally {
      setSaving(false)
    }
  }

  async function handleSave() {
    setSaving(true)
    setMessage(null)
    try {
      await put(`${BASE}/po/keywords`, { keywords, threshold })
      setMessage({ ok: true, text: 'Saved.' })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Failed to save.' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p className="py-6 text-center text-sm text-ink-faint">Loading...</p>

  return (
    <div className="flex flex-col gap-4">
      <SearchBox
        value={search}
        onChange={setSearch}
        placeholder="Search PO keywords"
        aria-label="Search PO keywords"
        className="w-full sm:max-w-sm"
      />
      <div className="segmented-control">
        {pagination.pagedItems.map((k) => (
          <span
            key={k}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface/60 px-3 py-1 text-xs text-ink"
          >
            {editingKeyword === k ? (
              <input
                autoFocus
                value={editingValue}
                onChange={(event) => setEditingValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') commitKeywordEdit(k)
                  if (event.key === 'Escape') setEditingKeyword(null)
                }}
                className="w-36 border-0 bg-transparent px-1 text-xs outline-none"
              />
            ) : k}
            <button
              type="button"
              aria-label={`Edit keyword ${k}`}
              onClick={() => {
                if (editingKeyword === k) commitKeywordEdit(k)
                else {
                  setEditingKeyword(k)
                  setEditingValue(k)
                }
              }}
              className="grid h-6 w-6 place-items-center rounded-full text-ink-faint hover:bg-accent/10 hover:text-accent"
            >
              {editingKeyword === k ? <CheckCircle2 className="h-3 w-3" /> : <Pencil className="h-3 w-3" />}
            </button>
            <button
              type="button"
              onClick={() => setKeywords((prev) => prev.filter((x) => x !== k))}
              className="grid h-6 w-6 place-items-center rounded-full text-ink-faint hover:bg-red-500/10 hover:text-red-500"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        {keywords.length === 0 && <span className="text-sm text-ink-faint">No keywords set.</span>}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="keywords"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
      <div className="flex flex-col gap-2 sm:flex-row">
        <CreatableCombobox
          value={keywordInput}
          options={keywords}
          onChange={setKeywordInput}
          placeholder="Choose a similar keyword or type a new one"
          ariaLabel="Add a PO keyword"
          suggestionLabel="Existing PO keywords"
          className="flex-1"
        />
        <Button variant="secondary" onClick={addKeyword}>
          Add
        </Button>
      </div>
      <label className="flex flex-col gap-1.5 text-sm">
        <span className="font-medium text-ink-dim">Fuzzy match threshold ({threshold.toFixed(2)})</span>
        <input
          type="range"
          min={0.5}
          max={1}
          step={0.01}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          className="accent-accent"
        />
      </label>
      {message && (
        <p className={cn('text-sm', message.ok ? 'text-emerald-500' : 'text-red-500')}>{message.text}</p>
      )}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Button
          variant="secondary"
          icon={<RotateCcw className="h-4 w-4" />}
          disabled={saving}
          onClick={() => void handleResetKeywords()}
        >
          Reset to defaults
        </Button>
        <Button loading={saving} onClick={() => void handleSave()}>
          Save
        </Button>
      </div>
    </div>
  )
}

function PoExcludedPanel() {
  const [excluded, setExcluded] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [archivingPo, setArchivingPo] = useState<string | null>(null)
  const [editingPo, setEditingPo] = useState<string | null>(null)
  const [editingPoValue, setEditingPoValue] = useState('')
  const [savingPo, setSavingPo] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const filteredExcluded = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    return query ? excluded.filter((po) => po.toLocaleLowerCase().includes(query)) : excluded
  }, [excluded, search])
  const pagination = usePagination(filteredExcluded, 10, search)
  const busy = adding || archivingPo !== null || savingPo !== null || clearing

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await get<string[]>(`${BASE}/po/excluded`)
      setExcluded(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd() {
    const v = input.trim()
    if (!v) return
    setError(null)
    setAdding(true)
    try {
      const res = await post<string[]>(`${BASE}/po/excluded`, { po_number: v })
      setExcluded(res)
      setInput('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to add.')
    } finally {
      setAdding(false)
    }
  }

  async function handleArchive(po: string) {
    setError(null)
    setArchivingPo(po)
    try {
      const res = await del<string[]>(`${BASE}/po/excluded/${encodeURIComponent(po)}`)
      setExcluded(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to archive PO number.')
    } finally {
      setArchivingPo(null)
    }
  }

  async function handleEdit(po: string) {
    const replacement = editingPoValue.trim()
    if (!replacement) return
    setError(null)
    setSavingPo(po)
    try {
      const res = await put<string[]>(`${BASE}/po/excluded/${encodeURIComponent(po)}`, {
        po_number: replacement,
      })
      setExcluded(res)
      setEditingPo(null)
      setEditingPoValue('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update PO number.')
    } finally {
      setSavingPo(null)
    }
  }

  async function handleClearExcluded() {
    if (excluded.length === 0 || !window.confirm('Archive all excluded PO numbers?')) return
    setError(null)
    setClearing(true)
    try {
      const res = await del<string[]>(`${BASE}/po/excluded`)
      setExcluded(res)
      setEditingPo(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to clear excluded PO numbers.')
    } finally {
      setClearing(false)
    }
  }

  if (loading) return <p className="py-6 text-center text-sm text-ink-faint">Loading...</p>

  return (
    <div className="flex flex-col gap-4">
      {error && (
        <p
          role="alert"
          className="rounded-xl border border-red-500/20 bg-red-500/[0.07] px-4 py-3 text-sm text-red-500"
        >
          {error}
        </p>
      )}

      <section
        aria-labelledby="excluded-po-list-title"
        className="overflow-hidden rounded-2xl border border-border bg-surface/45 shadow-[0_18px_40px_-34px_rgba(var(--shadow-rgb),0.55)]"
      >
        <div className="flex flex-col gap-3 border-b border-border bg-bg-soft/45 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-accent/15 bg-accent/[0.08] text-accent">
              <ListChecks className="h-5 w-5" aria-hidden="true" />
            </span>
            <div>
              <h3 id="excluded-po-list-title" className="text-sm font-semibold text-ink">
                Excluded PO numbers
              </h3>
              <p className="mt-0.5 text-xs leading-5 text-ink-dim">
                These purchase orders are omitted during PO report generation.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 pl-[3.25rem] sm:pl-0">
            <p className="text-xs font-medium text-ink-dim">
              {formatIndianNumber(excluded.length)} active {excluded.length === 1 ? 'exclusion' : 'exclusions'}
            </p>
            <button
              type="button"
              disabled={busy || excluded.length === 0}
              onClick={() => void handleClearExcluded()}
              className="inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold text-red-500 transition hover:bg-red-500/[0.08] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Trash2 className="h-3.5 w-3.5" /> Clear all
            </button>
          </div>
        </div>

        <div className="border-b border-border px-4 py-3 sm:px-5">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search excluded PO numbers"
            aria-label="Search excluded PO numbers"
            className="w-full sm:max-w-sm"
          />
        </div>

        <div className="overflow-x-auto">
          <table className="w-full table-fixed border-collapse">
            <caption className="sr-only">Active excluded purchase-order numbers</caption>
            <thead className="bg-surface/55">
              <tr className="border-b border-border text-left">
                <th scope="col" className="hidden w-16 px-5 py-3 text-xs font-semibold text-ink-dim sm:table-cell">
                  No.
                </th>
                <th scope="col" className="px-4 py-3 text-xs font-semibold text-ink-dim sm:px-5">
                  PO number
                </th>
                <th scope="col" className="hidden w-52 px-5 py-3 text-xs font-semibold text-ink-dim md:table-cell">
                  Treatment
                </th>
                <th scope="col" className="w-36 px-4 py-3 text-right text-xs font-semibold text-ink-dim sm:px-5">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {pagination.pagedItems.map((po, index) => (
                <tr
                  key={po}
                  className="border-b border-border/75 transition-colors last:border-b-0 hover:bg-accent/[0.035]"
                >
                  <td className="hidden px-5 py-3.5 text-sm tabular-nums text-ink-faint sm:table-cell">
                    {pagination.startIndex + index + 1}
                  </td>
                  <td className="px-4 py-3.5 sm:px-5">
                    {editingPo === po ? (
                      <input
                        autoFocus
                        value={editingPoValue}
                        onChange={(event) => setEditingPoValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') void handleEdit(po)
                          if (event.key === 'Escape') setEditingPo(null)
                        }}
                        className="field-control min-h-9 w-full py-1 text-sm"
                      />
                    ) : (
                      <span className="block truncate text-sm font-semibold tracking-[0.015em] text-ink" title={po}>
                        {po}
                      </span>
                    )}
                    <span className="mt-0.5 block text-xs text-ink-faint md:hidden">
                      Excluded from PO report
                    </span>
                  </td>
                  <td className="hidden px-5 py-3.5 text-sm text-ink-dim md:table-cell">
                    Excluded from PO report
                  </td>
                  <td className="px-4 py-3.5 text-right sm:px-5">
                    <button
                      type="button"
                      disabled={busy && editingPo !== po}
                      aria-label={`Edit excluded PO ${po}`}
                      title="Edit this PO number"
                      onClick={() => {
                        if (editingPo === po) void handleEdit(po)
                        else {
                          setEditingPo(po)
                          setEditingPoValue(po)
                        }
                      }}
                      className="mr-1 inline-flex min-h-9 items-center justify-center rounded-lg border border-transparent px-2 text-xs font-semibold text-ink-dim transition hover:border-accent/20 hover:bg-accent/[0.07] hover:text-accent disabled:opacity-45"
                    >
                      {savingPo === po
                        ? <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                        : editingPo === po
                          ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                          : <Pencil className="h-3.5 w-3.5" aria-hidden="true" />}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Archive excluded PO ${po}`}
                      aria-busy={archivingPo === po}
                      title="Archive this exclusion"
                      onClick={() => void handleArchive(po)}
                      className="inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-transparent px-2.5 text-xs font-semibold text-ink-dim transition hover:border-red-500/20 hover:bg-red-500/[0.07] hover:text-red-500 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      {archivingPo === po ? (
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      ) : (
                        <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                      Archive
                    </button>
                  </td>
                </tr>
              ))}
              {filteredExcluded.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-5 py-10 text-center">
                    <ListChecks className="mx-auto h-6 w-6 text-ink-faint" aria-hidden="true" />
                    <p className="mt-2 text-sm font-medium text-ink">
                      {excluded.length === 0 ? 'No excluded PO numbers' : 'No matching PO numbers'}
                    </p>
                    <p className="mt-1 text-xs text-ink-dim">
                      {excluded.length === 0
                        ? 'Add a PO number below when it should be omitted from the report.'
                        : 'Try a different search term.'}
                    </p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="excluded PO numbers"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />

      <form
        onSubmit={(event) => {
          event.preventDefault()
          void handleAdd()
        }}
        className="rounded-2xl border border-border bg-bg-soft/35 p-4 sm:p-5"
      >
        <label htmlFor="excluded-po-number" className="text-sm font-semibold text-ink">
          Add an excluded PO number
        </label>
        <p id="excluded-po-help" className="mt-1 text-xs leading-5 text-ink-dim">
          Enter the complete PO number exactly as it appears in the source data.
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <CreatableCombobox
            id="excluded-po-number"
            value={input}
            options={excluded}
            onChange={setInput}
            placeholder="Choose a similar PO or type a new number"
            ariaLabel="Add an excluded PO number"
            ariaDescribedBy="excluded-po-help"
            suggestionLabel="Existing excluded PO numbers"
            className="flex-1"
          />
          <Button type="submit" variant="secondary" loading={adding} disabled={busy || !input.trim()}>
            Add exclusion
          </Button>
        </div>
      </form>
    </div>
  )
}

const MAPPINGS_SECTIONS = [
  { key: 'location-incharge', label: 'Location → Incharge' },
  { key: 'site-overrides', label: 'Supplier Site Overrides' },
  { key: 'creator', label: 'Created-By Mapping' },
  { key: 'po-keywords', label: 'PO Keywords' },
  { key: 'po-excluded', label: 'Excluded PO Numbers' },
] as const

function MappingsTab() {
  const [activeSection, setActiveSection] = useState(0)

  const activeKey = MAPPINGS_SECTIONS[activeSection].key
  const tableConfig = UNACCOUNTED_MAPPING_CONFIGS.find((c) => c.key === activeKey)

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-ink-dim">
        This centralized database is the source of truth for all three report types.
      </p>

      <div className="flex flex-wrap gap-2">
        {MAPPINGS_SECTIONS.map((s, i) => (
          <button
            key={s.key}
            onClick={() => setActiveSection(i)}
            className={cn(
              'rounded-xl px-3.5 py-2 text-xs font-semibold transition duration-200',
              activeSection === i
                ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
            )}
          >
            {s.label}
          </button>
        ))}
      </div>

      {tableConfig && <UnaccountedMappingSection key={tableConfig.key} config={tableConfig} />}
      {activeKey === 'po-keywords' && <PoKeywordsPanel />}
      {activeKey === 'po-excluded' && <PoExcludedPanel />}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

const TOP_TABS = [
  { key: 'unaccounted', label: 'Unaccounted Transactions' },
  { key: 'mrn', label: 'Pending MRN' },
  { key: 'po', label: 'Uninvoiced Expense PO' },
  { key: 'mail', label: 'Mail' },
  { key: 'mappings', label: 'Mappings' },
] as const

export default function UnaccountedTransactions() {
  const [tab, setTab] = useState<(typeof TOP_TABS)[number]['key']>('unaccounted')
  const [knownLocations, setKnownLocations] = useState<string[]>([])

  useEffect(() => {
    void get<string[]>(`${BASE}/mappings/known-locations`)
      .then(setKnownLocations)
      .catch(() => {
        // Non-critical: fix-forms fall back to free text.
      })
  }, [])

  return (
    <AppShell title="Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <ListChecks className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Exception intelligence</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Unaccounted Transactions, Pending MRN &amp; Uninvoiced Expense POs Report Generator
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Generate reports across three sub-report types, manage mappings, or send them all
                by email.
              </p>
            </div>
          </div>

          <div className="segmented-control">
            {TOP_TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cn(
                  'rounded-xl px-4 py-2 text-sm font-semibold transition duration-200',
                  tab === t.key
                    ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                    : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'unaccounted' && <UnaccountedTab knownLocations={knownLocations} />}
          {tab === 'mrn' && <MrnTab knownLocations={knownLocations} />}
          {tab === 'po' && <PoTab knownLocations={knownLocations} />}
          {tab === 'mail' && (
            <MailTab knownLocations={knownLocations} onManageMappings={() => setTab('mappings')} />
          )}
          {tab === 'mappings' && <MappingsTab />}
        </GlassCard>
      </div>
    </AppShell>
  )
}
