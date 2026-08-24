import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  Receipt,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { Pagination } from '@/components/Pagination'
import { MonthYearPicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianDate, formatIndianNumber, getIndianMonthInputValue } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/rdc-payables'

// ── report generation ────────────────────────────────────────────────────

interface ProcessResult {
  output_path: string
  download_filename: string
  unmapped_vendor_sites: string[]
  row_count: number
  raw_row_count: number
  matched_count: number
  unmatched_count: number
  transaction_type_counts: Record<string, number>
  aging_bucket_counts: Record<string, number>
  log: string[]
  reporting_ref_date: string | null
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

// ── mapping table configs ────────────────────────────────────────────────

interface MappingConfig {
  key: string
  title: string
  addLabel: string
  columns: MappingColumn[]
  buildKey: (row: MappingRow) => string
  buildBody: (row: MappingRow) => Record<string, unknown>
}

function compositeKey(vendor: string, invoice: string): string {
  return encodeURIComponent(`${vendor}|${invoice ?? ''}`)
}

const MAPPING_CONFIGS: MappingConfig[] = [
  {
    key: 'vendor-site-codes',
    title: 'Vendor Site Codes',
    addLabel: 'Add vendor site code',
    columns: [
      { key: 'vendor_site_code', label: 'Vendor Site Code' },
      { key: 'region', label: 'Region' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => encodeURIComponent(row.vendor_site_code ?? ''),
    buildBody: (row) => ({
      vendor_site_code: row.vendor_site_code ?? '',
      region: row.region ?? '',
      accounts_incharge: row.accounts_incharge || undefined,
    }),
  },
  {
    key: 'location-codes',
    title: 'Location Codes',
    addLabel: 'Add location code',
    columns: [
      { key: 'location_code', label: 'Location Code' },
      { key: 'region', label: 'Region' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => encodeURIComponent(row.location_code ?? ''),
    buildBody: (row) => ({
      location_code: row.location_code ?? '',
      region: row.region ?? '',
      accounts_incharge: row.accounts_incharge || undefined,
    }),
  },
  {
    key: 'row-exclusions',
    title: 'Row Exclusions',
    addLabel: 'Add row exclusion',
    columns: [
      { key: 'vendor_number', label: 'Vendor Number' },
      { key: 'invoice_number', label: 'Invoice Number (blank = vendor-wide)' },
    ],
    buildKey: (row) => compositeKey(row.vendor_number ?? '', row.invoice_number ?? ''),
    buildBody: (row) => ({
      vendor_number: row.vendor_number ?? '',
      invoice_number: row.invoice_number || '',
    }),
  },
  {
    key: 'invoice-overrides',
    title: 'Invoice Overrides',
    addLabel: 'Add invoice override',
    columns: [
      { key: 'vendor_number', label: 'Vendor Number' },
      { key: 'invoice_number', label: 'Invoice Number' },
      { key: 'region', label: 'Region' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => compositeKey(row.vendor_number ?? '', row.invoice_number ?? ''),
    buildBody: (row) => ({
      vendor_number: row.vendor_number ?? '',
      invoice_number: row.invoice_number || '',
      region: row.region ?? '',
      accounts_incharge: row.accounts_incharge || undefined,
    }),
  },
  {
    key: 'region-incharge',
    title: 'Region Incharge',
    addLabel: 'Add region incharge',
    columns: [
      { key: 'region', label: 'Region' },
      { key: 'accounts_incharge', label: 'Accounts Incharge' },
    ],
    buildKey: (row) => encodeURIComponent(row.region ?? ''),
    buildBody: (row) => ({
      region: row.region ?? '',
      accounts_incharge: row.accounts_incharge ?? '',
    }),
  },
  {
    key: 'transaction-type-overrides',
    title: 'Transaction Type Overrides',
    addLabel: 'Add transaction type override',
    columns: [
      { key: 'vendor_number', label: 'Vendor Number' },
      { key: 'invoice_number', label: 'Invoice Number' },
      { key: 'transaction_type', label: 'Transaction Type' },
    ],
    buildKey: (row) => compositeKey(row.vendor_number ?? '', row.invoice_number ?? ''),
    buildBody: (row) => ({
      vendor_number: row.vendor_number ?? '',
      invoice_number: row.invoice_number || '',
      transaction_type: row.transaction_type ?? '',
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
      const data = await get<MappingRow[]>(`${BASE}/${config.key}`)
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
    await post(`${BASE}/${config.key}`, config.buildBody(row))
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    const original = rows[index]
    await put(`${BASE}/${config.key}/${config.buildKey(original)}`, config.buildBody(row))
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    await del(`${BASE}/${config.key}/${config.buildKey(original)}`)
    await load()
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
        />
      )}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────

export default function RdcPayables() {
  const [file, setFile] = useState<File | null>(null)
  const [cutoffPeriod, setCutoffPeriod] = useState(getIndianMonthInputValue)
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [processError, setProcessError] = useState<string | null>(null)
  const [activityLog, setActivityLog] = useState<string[]>([])

  const [fixForms, setFixForms] = useState<Record<string, { region: string; accounts_incharge: string }>>({})
  const [fixedSites, setFixedSites] = useState<Record<string, boolean>>({})
  const [fixingSite, setFixingSite] = useState<string | null>(null)

  const [knownRegions, setKnownRegions] = useState<string[]>([])

  const [activeMappingTab, setActiveMappingTab] = useState(0)

  useEffect(() => {
    void get<MappingRow[]>(`${BASE}/region-incharge`)
      .then((rows) => setKnownRegions(rows.map((r) => r.region).filter(Boolean)))
      .catch(() => {
        // Non-critical: the region input just falls back to free text.
      })
  }, [])

  async function handleGenerate() {
    if (!file) return
    const [cutoffYear, cutoffMonth] = cutoffPeriod.split('-').map(Number)
    if (!cutoffYear || cutoffMonth < 1 || cutoffMonth > 12) {
      setProcessError('Choose a valid cutoff month and year.')
      return
    }
    setSubmitting(true)
    setProcessError(null)
    setResult(null)
    setJobId(null)
    setFixedSites({})
    setFixForms({})
    setActivityLog([`[INFO] Queuing ${file.name} for processing...`])
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('cutoff_year', String(cutoffYear))
      formData.append('cutoff_month', String(cutoffMonth))
      const res = await postForm<{ job_id: string }>(`${BASE}/process`, formData)
      setJobId(res.job_id)
      // submitting stays true until the background job itself settles
      // (see ProgressPanel's onDone/onError below) - the request returning
      // just means the job was queued, not that it's finished.
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Failed to start report generation.'
      setProcessError(message)
      setActivityLog((previous) => [...previous, `[ERR] ${message}`])
      setSubmitting(false)
    }
  }

  function handleDownloadReport() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = result?.download_filename || 'Payables_Report.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  async function handleFixSite(site: string) {
    const form = fixForms[site]
    if (!form?.region?.trim()) return
    setFixingSite(site)
    setProcessError(null)
    try {
      await post(`${BASE}/mappings/fix`, {
        vendor_site_code: site,
        region: form.region.trim(),
        accounts_incharge: form.accounts_incharge?.trim() || undefined,
      })
      setFixedSites((prev) => ({ ...prev, [site]: true }))
    } catch (err) {
      setProcessError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixingSite(null)
    }
  }

  const unmappedSites = result?.unmapped_vendor_sites ?? []
  const allFixed = unmappedSites.length > 0 && unmappedSites.every((s) => fixedSites[s])
  const unmappedPagination = usePagination(unmappedSites, 10, jobId)

  return (
    <AppShell title="RDC Payables Report">
      <div className="flex flex-col gap-6">
        {/* ── Report generation ──────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Receipt className="h-5 w-5" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Payables intelligence</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Generate payables report</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload an Oracle ERP export and pick the GL cutoff period.
              </p>
            </div>
          </div>

          <FileDropzone
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop the ERP export here, or click to browse"
            files={file ? [file] : []}
            onFilesSelected={(f) => setFile(f[0] ?? null)}
            onRemove={() => setFile(null)}
          />

          <div className="flex flex-col items-stretch gap-4 sm:flex-row sm:flex-wrap sm:items-end">
            <label className="flex w-full flex-col gap-1.5 text-sm sm:w-64">
              <span className="font-medium text-ink-dim">GL cutoff month and year</span>
              <MonthYearPicker
                value={cutoffPeriod}
                onValueChange={setCutoffPeriod}
                aria-label="GL cutoff month and year"
              />
            </label>
            <Button
              className="sm:ml-auto"
              onClick={() => void handleGenerate()}
              loading={submitting}
              disabled={!file || !/^\d{4}-(0[1-9]|1[0-2])$/.test(cutoffPeriod)}
            >
              Generate report
            </Button>
          </div>

          {processError && (
            <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {processError}
            </p>
          )}

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
                setActivityLog((previous) => [...previous, `[ERR] ${error}`])
                setSubmitting(false)
              }}
            />
          )}

          {result && (
            <div className="subpanel flex flex-col gap-4 p-4">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ['Rows parsed', result.raw_row_count, 'text-ink'],
                  ['Output rows', result.row_count, 'text-accent'],
                  ['Vendor matched', result.matched_count, 'text-emerald-500'],
                  ['Unmatched', result.unmatched_count, 'text-amber-500'],
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
                  <span className="text-ink-faint">Reporting reference date</span>
                  <p className="text-ink">
                    {result.reporting_ref_date ? formatIndianDate(result.reporting_ref_date) : '—'}
                  </p>
                </div>
                <Button
                  icon={<FileSpreadsheet className="h-4 w-4" />}
                  onClick={handleDownloadReport}
                >
                  Save / download report
                </Button>
              </div>

              {unmappedSites.length > 0 && (
                <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
                  <div className="flex items-center gap-2 text-amber-600">
                    <AlertTriangle className="h-4 w-4" />
                    <h4 className="font-display text-sm font-semibold">
                      Missing mappings ({formatIndianNumber(unmappedSites.length)} vendor site
                      {unmappedSites.length === 1 ? '' : 's'})
                    </h4>
                  </div>
                  <p className="text-sm text-ink-dim">
                    These Vendor Site Codes had no Region mapped. Assign a Region (and optionally
                    an Accounts Incharge) for each, then regenerate the report.
                  </p>
                  <datalist id="rdc-known-regions">
                    {knownRegions.map((r) => (
                      <option key={r} value={r} />
                    ))}
                  </datalist>
                  <div className="flex flex-col gap-2">
                    {unmappedPagination.pagedItems.map((site) => {
                      const form = fixForms[site] ?? { region: '', accounts_incharge: '' }
                      const fixed = Boolean(fixedSites[site])
                      return (
                        <div
                          key={site}
                          className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
                        >
                          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[10rem]">
                            {site}
                          </span>
                          <input
                            list="rdc-known-regions"
                            placeholder="Region"
                            value={form.region}
                            disabled={fixed}
                            onChange={(e) =>
                              setFixForms((prev) => ({
                                ...prev,
                                [site]: { ...form, region: e.target.value },
                              }))
                            }
                            className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-36"
                          />
                          <input
                            placeholder="Accounts Incharge (optional)"
                            value={form.accounts_incharge}
                            disabled={fixed}
                            onChange={(e) =>
                              setFixForms((prev) => ({
                                ...prev,
                                [site]: { ...form, accounts_incharge: e.target.value },
                              }))
                            }
                            className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-56"
                          />
                          {fixed ? (
                            <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                              <CheckCircle2 className="h-4 w-4" /> Saved
                            </span>
                          ) : (
                            <Button
                              variant="secondary"
                              loading={fixingSite === site}
                              disabled={!form.region.trim()}
                              onClick={() => void handleFixSite(site)}
                            >
                              Fix
                            </Button>
                          )}
                        </div>
                      )
                    })}
                  </div>
                  <Pagination
                    page={unmappedPagination.page}
                    pageCount={unmappedPagination.pageCount}
                    pageSize={unmappedPagination.pageSize}
                    totalItems={unmappedPagination.totalItems}
                    itemLabel="unmapped sites"
                    onPageChange={unmappedPagination.setPage}
                    onPageSizeChange={unmappedPagination.setPageSize}
                  />
                  <div className="flex justify-stretch sm:justify-end">
                    <Button
                      variant="primary"
                      icon={<RefreshCw className="h-4 w-4" />}
                      disabled={!allFixed}
                      onClick={() => void handleGenerate()}
                    >
                      Regenerate report
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {activityLog.length > 0 && (
            <div className="overflow-hidden rounded-2xl border border-stroke/70 bg-slate-950 shadow-inner">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
                  <span className="h-2 w-2 rounded-full bg-emerald-400" />
                  Processing log
                </div>
                <button
                  type="button"
                  onClick={() => setActivityLog([])}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-400 transition hover:bg-white/10 hover:text-white"
                >
                  <Trash2 className="h-3.5 w-3.5" /> Clear log
                </button>
              </div>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-xs leading-6 text-slate-300">
                {activityLog.join('\n')}
              </pre>
            </div>
          )}
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
