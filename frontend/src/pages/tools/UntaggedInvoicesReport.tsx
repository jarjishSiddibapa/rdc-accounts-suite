import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  RefreshCw,
  Tags,
  Trash2,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { DatePicker } from '@/components/TemporalPicker'
import { Pagination } from '@/components/Pagination'
import { LoadingNotice } from '@/components/LoadingNotice'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/untagged-invoices-report'

// ── report generation ────────────────────────────────────────────────────

interface ValidationWarning {
  category: string
  items: string[]
}

interface ProcessResult {
  output_path: string
  download_filename: string
  as_on_date: string
  total_rows: number
  below_1k_row_count: number
  untagged_detail_row_count: number
  untagged_summary_row_count: number
  validation_warnings: ValidationWarning[]
  log: [string, string][]
}

interface ProcessJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: ProcessResult | null
  error: string | null
}

async function pollProcessJob(jobId: string): Promise<JobState<ProcessResult>> {
  const job = await get<ProcessJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

// ── activity log (tuple-based, matches the router's _LogQueue.messages) ───

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

function LogPanel({ log, onClear }: { log: [string, string][]; onClear: () => void }) {
  const pagination = usePagination(log ?? [], 10)
  if (!log || log.length === 0) return null
  return (
    <div className="overflow-hidden rounded-2xl border border-stroke/70 bg-slate-950 shadow-inner">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <span className="h-2 w-2 rounded-full bg-emerald-400" />
          Processing log
        </div>
        <button
          type="button"
          onClick={onClear}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-400 transition hover:bg-white/10 hover:text-white"
        >
          <Trash2 className="h-3.5 w-3.5" /> Clear log
        </button>
      </div>
      <div className="flex flex-col gap-3 px-4 py-3 font-mono text-xs leading-relaxed">
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
    </div>
  )
}

// ── missing-mapping fix flow (mirrors UnappliedReceipts' ValidationWarnings) ─

interface WarningFixConfig {
  fieldLabel: string
  mappingKey: string
  buildBody: (item: string, value: string) => Record<string, unknown>
}

const WARNING_FIX_CONFIGS: Record<string, WarningFixConfig> = {
  'Location Not Mapped': {
    fieldLabel: 'Location',
    mappingKey: 'locations',
    buildBody: (item, value) => ({ location_name: item, location: value }),
  },
  'Accounts Incharge Not Mapped': {
    fieldLabel: 'Accounts Incharge',
    mappingKey: 'accounts-incharge',
    buildBody: (item, value) => ({ location: item, accounts_incharge: value }),
  },
}

function WarningCategoryFix({
  category,
  items,
  suggestions,
  fixed,
  onFixed,
}: {
  category: string
  items: string[]
  suggestions: Record<string, string[]>
  fixed: Record<string, boolean>
  onFixed: (key: string) => void
}) {
  const config = WARNING_FIX_CONFIGS[category]
  const [forms, setForms] = useState<Record<string, string>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(items, 10)

  async function handleFix(item: string) {
    if (!config) return
    const value = forms[item]?.trim()
    if (!value) return
    const key = `${category}::${item}`
    setFixing(item)
    setError(null)
    try {
      await post(`${BASE}/mappings/${config.mappingKey}`, config.buildBody(item, value))
      onFixed(key)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  if (!config) {
    return (
      <div className="subpanel flex flex-col gap-2 p-3">
        <span className="text-xs font-bold tracking-[0.08em] text-amber-600 uppercase">
          {category} ({formatIndianNumber(items.length)})
        </span>
        <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
          {items.map((item) => (
            <span
              key={item}
              className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-ink"
            >
              {item}
            </span>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="subpanel flex flex-col gap-2 p-3">
      <span className="text-xs font-bold tracking-[0.08em] text-amber-600 uppercase">
        {category} ({formatIndianNumber(items.length)})
      </span>
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((item) => {
          const key = `${category}::${item}`
          const isFixed = Boolean(fixed[key])
          return (
            <div
              key={item}
              className="flex flex-col items-stretch gap-2 rounded-lg border border-amber-500/20 bg-bg-soft/40 px-3 py-2 sm:flex-row sm:flex-wrap sm:items-center"
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">
                {item}
              </span>
              <CreatableCombobox
                placeholder={config.fieldLabel}
                value={forms[item] ?? ''}
                options={suggestions[config.mappingKey] ?? []}
                disabled={isFixed}
                onChange={(value) => setForms((prev) => ({ ...prev, [item]: value }))}
                ariaLabel={`${config.fieldLabel} for ${item}`}
                suggestionLabel={`Existing ${config.fieldLabel.toLocaleLowerCase()} values`}
                className="w-full sm:w-64"
              />
              {isFixed ? (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" /> Saved
                </span>
              ) : (
                <Button
                  variant="secondary"
                  loading={fixing === item}
                  disabled={!forms[item]?.trim()}
                  onClick={() => void handleFix(item)}
                >
                  Fix
                </Button>
              )}
            </div>
          )
        })}
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel={category.toLowerCase()}
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

function ValidationWarnings({
  warnings,
  suggestions,
  onRegenerate,
  regenerating,
}: {
  warnings: ValidationWarning[]
  suggestions: Record<string, string[]>
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  if (warnings.length === 0) return null

  const allKeys = warnings.flatMap((w) => w.items.map((item) => `${w.category}::${item}`))
  const allFixed = allKeys.length > 0 && allKeys.every((k) => fixed[k])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing mappings. The workbook was still written with these left blank
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        Fix each unmapped value below, then regenerate without re-uploading the file.
      </p>
      {warnings.map((warning) => (
        <WarningCategoryFix
          key={warning.category}
          category={warning.category}
          items={warning.items}
          suggestions={suggestions}
          fixed={fixed}
          onFixed={(key) => setFixed((prev) => ({ ...prev, [key]: true }))}
        />
      ))}
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

// ── mapping table configs ────────────────────────────────────────────────

interface MappingConfig {
  key: string
  title: string
  addLabel: string
  columns: MappingColumn[]
  buildKey: (row: MappingRow) => string
  buildBody: (row: MappingRow) => Record<string, unknown>
}

const MAPPING_CONFIGS: MappingConfig[] = [
  {
    key: 'locations',
    title: 'Location Name',
    addLabel: 'Add location name',
    columns: [
      { key: 'location_name', label: 'Location Name (raw)' },
      { key: 'location', label: 'Location' },
    ],
    buildKey: (row) => encodeURIComponent(row.location_name ?? ''),
    buildBody: (row) => ({
      location_name: row.location_name ?? '',
      location: row.location ?? '',
    }),
  },
  {
    key: 'accounts-incharge',
    title: 'Accounts Incharge',
    addLabel: 'Add accounts incharge',
    columns: [
      { key: 'location', label: 'Location' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => encodeURIComponent(row.location ?? ''),
    buildBody: (row) => ({
      location: row.location ?? '',
      accounts_incharge: row.accounts_incharge ?? '',
    }),
  },
]

function MappingSection({ config }: { config: MappingConfig }) {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get<MappingRow[]>(`${BASE}/mappings/${config.key}`)
      setRows(data)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load mapping table.')
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
    await put(`${BASE}/mappings/${config.key}/${config.buildKey(original)}`, config.buildBody(row))
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    await del(`${BASE}/mappings/${config.key}/${config.buildKey(original)}`)
    await load()
  }

  return (
    <div className="flex flex-col gap-3">
      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {loading ? (
        <LoadingNotice />
      ) : (
        <MappingTable
          title={config.title}
          addLabel={config.addLabel}
          columns={config.columns}
          rows={rows}
          onAdd={handleAdd}
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

export default function UntaggedInvoicesReport() {
  const [file, setFile] = useState<File | null>(null)
  const [asOnDate, setAsOnDate] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [processError, setProcessError] = useState<string | null>(null)
  const [activityLog, setActivityLog] = useState<[string, string][]>([])

  const [activeMappingTab, setActiveMappingTab] = useState(0)
  const [mappingSuggestions, setMappingSuggestions] = useState<Record<string, string[]>>({})

  useEffect(() => {
    void Promise.all([
      get<MappingRow[]>(`${BASE}/mappings/locations`),
      get<MappingRow[]>(`${BASE}/mappings/accounts-incharge`),
    ])
      .then(([locationRows, inchargeRows]) => {
        setMappingSuggestions({
          locations: [...new Set(locationRows.map((row) => row.location).filter(Boolean))],
          'accounts-incharge': [
            ...new Set(inchargeRows.map((row) => row.accounts_incharge).filter(Boolean)),
          ],
        })
      })
      .catch(() => {
        // Non-critical: the mapping inputs still accept new free-text values.
      })
  }, [])

  function handleFileSelected(files: File[]) {
    setFile(files[0] ?? null)
  }

  async function handleProcess() {
    if (!file) return
    setSubmitting(true)
    setProcessError(null)
    setResult(null)
    setJobId(null)
    setActivityLog([['info', 'Queuing report for processing...']])
    try {
      const fd = new FormData()
      fd.append('file', file)
      if (asOnDate) fd.append('as_on_date', asOnDate)
      const res = await postForm<{ job_id: string }>(`${BASE}/process`, fd)
      setJobId(res.job_id)
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not start report generation.'
      setProcessError(message)
      setActivityLog((previous) => [...previous, ['error', message]])
      setSubmitting(false)
    }
  }

  function handleDownloadReport() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = result?.download_filename || 'Untagged_Invoices_Report.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  const canProcess = Boolean(file)
  const validationWarnings = result?.validation_warnings ?? []

  return (
    <AppShell title="Untagged Invoices Report Generator">
      <div className="flex flex-col gap-6">
        {/* ── Report generation ──────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Tags className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">
                AR untagged receipts
              </p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Generate untagged invoices report
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload the Aging export to generate the formatted workbook (Untagged Summary,
                Untagged Detailed Ageing, Ageing and Below 1k sheets).
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium text-ink-dim">Aging export</span>
            <FileDropzone
              accept=".xlsx"
              label="Drag & drop the Aging export here, or click to browse"
              files={file ? [file] : []}
              onFilesSelected={handleFileSelected}
              onRemove={() => setFile(null)}
            />
          </div>

          <label className="flex flex-col gap-2 sm:max-w-xs">
            <span className="text-sm font-medium text-ink-dim">As on date (optional)</span>
            <DatePicker value={asOnDate} onValueChange={setAsOnDate} aria-label="As on date" />
            <span className="text-xs text-ink-faint">
              Ageing buckets are computed from the invoice/receipt date against this date.
              Defaults to today if left blank.
            </span>
          </label>

          <div className="flex justify-stretch sm:justify-end">
            <Button onClick={() => void handleProcess()} loading={submitting} disabled={!canProcess}>
              Generate report
            </Button>
          </div>

          {processError && <ErrorBanner className="whitespace-pre-wrap">{processError}</ErrorBanner>}

          {jobId && (
            <ProgressPanel
              jobId={jobId}
              poller={pollProcessJob}
              onDone={(res) => {
                setResult(res ?? null)
                if (res?.log) setActivityLog(res.log)
                setSubmitting(false)
              }}
              onError={(error) => {
                setProcessError(error)
                setActivityLog((previous) => [...previous, ['error', error]])
                setSubmitting(false)
              }}
              onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
            />
          )}

          {result && (
            <div className="subpanel flex flex-col gap-4 p-4">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['Rows in Ageing', result.total_rows, 'text-ink'],
                  ['Below 1k locations', result.below_1k_row_count, 'text-accent'],
                  ['Untagged detail rows', result.untagged_detail_row_count, 'text-amber-500'],
                  ['Untagged Summary locations', result.untagged_summary_row_count, 'text-emerald-500'],
                ].map(([label, value, color]) => (
                  <div key={String(label)} className="rounded-xl border border-stroke/70 bg-surface/55 px-4 py-3">
                    <span className="text-xs font-medium text-ink-faint">{label}</span>
                    <p className={`mt-1 font-display text-2xl font-semibold ${color}`}>
                      {formatIndianNumber(Number(value))}
                    </p>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <div className="mr-auto">
                  <span className="text-ink-faint">Output file</span>
                  <p className="text-ink">{result.download_filename}</p>
                </div>
                <Button icon={<FileSpreadsheet className="h-4 w-4" />} onClick={handleDownloadReport}>
                  Save / download report
                </Button>
              </div>

              <ValidationWarnings
                warnings={validationWarnings}
                suggestions={mappingSuggestions}
                regenerating={submitting}
                onRegenerate={() => void handleProcess()}
              />
            </div>
          )}

          <LogPanel log={activityLog} onClear={() => setActivityLog([])} />
        </GlassCard>

        {/* ── Mapping tables ─────────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div>
            <h2 className="font-display text-lg font-semibold text-ink">Mapping tables</h2>
            <p className="text-sm text-ink-dim">
              This centralized database is the source of truth. Manage entries directly below.
            </p>
          </div>

          <div className="segmented-control">
            {MAPPING_CONFIGS.map((cfg, i) => (
              <button
                key={cfg.key}
                onClick={() => setActiveMappingTab(i)}
                className={cn(
                  'rounded-xl px-4 py-2 text-sm font-semibold transition duration-200',
                  activeMappingTab === i
                    ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                    : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
                )}
              >
                {cfg.title}
              </button>
            ))}
          </div>

          <MappingSection key={activeMappingTab} config={MAPPING_CONFIGS[activeMappingTab]} />
        </GlassCard>
      </div>
    </AppShell>
  )
}
