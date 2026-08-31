import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2,
  Download,
  FileSpreadsheet,
  MapPinned,
  RotateCcw,
  Terminal,
  TriangleAlert,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/Button'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { FileDropzone } from '@/components/FileDropzone'
import { GlassCard } from '@/components/GlassCard'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { Pagination } from '@/components/Pagination'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { LoadingNotice } from '@/components/LoadingNotice'
import { DatePicker } from '@/components/TemporalPicker'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/creditors-ageing'

const MAPPING_COLUMNS: MappingColumn[] = [
  { key: 'vendor_name', label: 'Vendor name' },
  { key: 'location', label: 'Location' },
  { key: 'vendor_type', label: 'Vendor type' },
  { key: 'vendor_sub_type', label: 'Vendor sub type' },
  { key: 'intercompany', label: 'Intercompany' },
]

interface ReportResult {
  output_path: string
  download_filename: string
  new_vendors_csv_path: string | null
  as_on_date: string
  as_on_label: string
  ageing_through_date: string
  new_vendors: string[]
  new_vendor_guesses: Record<string, string>
  counts: {
    only_creditors: number
    advances: number
    intercompany: number
  }
  tb_ledgers: number
  bill_wise_rows: number
  log: [string, string][]
}

interface JobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: ReportResult | null
  error: string | null
}

interface ClassificationDraft {
  location: string
  vendor_type: string
  vendor_sub_type: string
  intercompany: boolean
}

function intercompanyValue(value: string | undefined): boolean {
  return ['yes', 'true', '1', 'y'].includes((value ?? '').trim().toLowerCase())
}

function mappingPayload(row: MappingRow) {
  return {
    vendor_name: row.vendor_name ?? '',
    location: row.location ?? '',
    vendor_type: row.vendor_type ?? '',
    vendor_sub_type: row.vendor_sub_type ?? '',
    intercompany: intercompanyValue(row.intercompany),
  }
}

async function pollJob(jobId: string): Promise<JobState<ReportResult>> {
  const job = await get<JobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

function logColor(level: string): string {
  switch (level.toLowerCase()) {
    case 'success':
    case 'ok':
      return 'text-emerald-400'
    case 'warning':
    case 'warn':
      return 'text-amber-400'
    case 'error':
      return 'text-red-400'
    default:
      return 'text-sky-400'
  }
}

function MappingManager({
  rows,
  archivedRows,
  loading,
  archivedLoading,
  error,
  onReload,
  onLoadArchived,
}: {
  rows: MappingRow[]
  archivedRows: MappingRow[]
  loading: boolean
  archivedLoading: boolean
  error: string | null
  onReload: () => Promise<void>
  onLoadArchived: () => Promise<void>
}) {
  async function add(row: MappingRow) {
    await post(`${BASE}/mappings`, mappingPayload(row))
    await onReload()
  }

  async function edit(index: number, row: MappingRow) {
    const original = rows[index]
    await put(`${BASE}/mappings/${encodeURIComponent(original.vendor_name ?? '')}`, mappingPayload(row))
    await onReload()
  }

  async function archive(index: number) {
    await del(`${BASE}/mappings/${encodeURIComponent(rows[index].vendor_name ?? '')}`)
    await onReload()
  }

  async function restore(index: number) {
    await post(`${BASE}/mappings/${encodeURIComponent(archivedRows[index].vendor_name ?? '')}/restore`)
    await Promise.all([onReload(), onLoadArchived()])
  }

  if (loading) {
    return <LoadingNotice detail="Loading centralized vendor mappings." />
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{error}</p>}
      <MappingTable
        title="Vendor classification mapping"
        addLabel="Add vendor"
        columns={MAPPING_COLUMNS}
        rows={rows}
        onAdd={add}
        onEdit={edit}
        onDelete={archive}
        archive={{
          rows: archivedRows,
          loading: archivedLoading,
          onOpen: onLoadArchived,
          onRestore: restore,
        }}
      />
    </div>
  )
}

function NewVendorClassification({
  result,
  mappings,
  jobId,
  onMappingsChanged,
  onRegenerate,
  regenerating,
}: {
  result: ReportResult
  mappings: MappingRow[]
  jobId: string
  onMappingsChanged: () => Promise<void>
  onRegenerate: () => Promise<void>
  regenerating: boolean
}) {
  const [drafts, setDrafts] = useState<Record<string, ClassificationDraft>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(result.new_vendors, 10, result.as_on_date)

  useEffect(() => {
    setDrafts(Object.fromEntries(result.new_vendors.map((vendor) => [vendor, {
      location: result.new_vendor_guesses[vendor] ?? '',
      vendor_type: '',
      vendor_sub_type: '',
      intercompany: false,
    }])))
    setSaved(new Set())
  }, [result])

  const suggestions = useMemo(() => ({
    locations: [...new Set(mappings.map((row) => row.location).filter(Boolean))],
    vendorTypes: [...new Set(mappings.map((row) => row.vendor_type).filter(Boolean))],
    vendorSubTypes: [...new Set(mappings.map((row) => row.vendor_sub_type).filter(Boolean))],
  }), [mappings])

  function update(vendor: string, patch: Partial<ClassificationDraft>) {
    setDrafts((current) => ({
      ...current,
      [vendor]: { ...current[vendor], ...patch },
    }))
  }

  async function save(vendor: string) {
    const draft = drafts[vendor]
    if (!draft) return
    setSaving(vendor)
    setError(null)
    try {
      await post(`${BASE}/mappings`, {
        vendor_name: vendor,
        ...draft,
      })
      setSaved((current) => new Set(current).add(vendor))
      await onMappingsChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not save the mapping for ${vendor}.`)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="rounded-2xl border border-amber-500/30 bg-amber-500/[0.07] p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex gap-3">
          <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
          <div>
            <h3 className="font-display text-lg font-semibold text-ink">Classify new vendors</h3>
            <p className="mt-1 text-sm leading-6 text-ink-dim">
              The workbook was generated with blank classification fields for these vendors. Save each mapping, then regenerate using the same uploaded file.
            </p>
          </div>
        </div>
        {result.new_vendors_csv_path && (
          <Button
            variant="secondary"
            icon={<Download className="h-4 w-4" />}
            onClick={() => {
              window.location.href = apiUrl(`${BASE}/download/${jobId}/new-vendors`)
            }}
          >
            Download list
          </Button>
        )}
      </div>

      {error && <p className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>}

      <div className="mt-5 flex flex-col gap-3">
        {pagination.pagedItems.map((vendor) => {
          const draft = drafts[vendor] ?? {
            location: result.new_vendor_guesses[vendor] ?? '',
            vendor_type: '',
            vendor_sub_type: '',
            intercompany: false,
          }
          const isSaved = saved.has(vendor)
          return (
            <div key={vendor} className="subpanel grid gap-3 p-4 xl:grid-cols-[minmax(15rem,1.35fr)_repeat(3,minmax(10rem,1fr))_auto] xl:items-end">
              <div className="min-w-0">
                <p className="text-xs font-medium text-ink-faint">Vendor</p>
                <p className="mt-1 break-words text-sm font-semibold text-ink">{vendor}</p>
              </div>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Location</span>
                <CreatableCombobox
                  value={draft.location}
                  options={suggestions.locations}
                  onChange={(value) => update(vendor, { location: value })}
                  suggestionLabel="Existing locations"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Vendor type</span>
                <CreatableCombobox
                  value={draft.vendor_type}
                  options={suggestions.vendorTypes}
                  onChange={(value) => update(vendor, { vendor_type: value })}
                  suggestionLabel="Existing vendor types"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">Vendor sub type</span>
                <CreatableCombobox
                  value={draft.vendor_sub_type}
                  options={suggestions.vendorSubTypes}
                  onChange={(value) => update(vendor, { vendor_sub_type: value })}
                  suggestionLabel="Existing vendor sub types"
                />
              </label>
              <div className="flex flex-wrap items-center gap-3 xl:flex-col xl:items-stretch">
                <label className="flex min-h-11 items-center gap-2 rounded-xl border border-border bg-surface/55 px-3 text-sm text-ink-dim">
                  <input
                    type="checkbox"
                    checked={draft.intercompany}
                    onChange={(event) => update(vendor, { intercompany: event.target.checked })}
                    className="h-4 w-4 accent-[var(--color-accent)]"
                  />
                  Intercompany
                </label>
                <Button
                  variant={isSaved ? 'secondary' : 'primary'}
                  loading={saving === vendor}
                  disabled={isSaved}
                  icon={isSaved ? <CheckCircle2 className="h-4 w-4" /> : undefined}
                  onClick={() => void save(vendor)}
                >
                  {isSaved ? 'Saved' : 'Save mapping'}
                </Button>
              </div>
            </div>
          )
        })}
      </div>

      <div className="mt-4">
        <Pagination
          page={pagination.page}
          pageCount={pagination.pageCount}
          pageSize={pagination.pageSize}
          totalItems={pagination.totalItems}
          itemLabel="new vendors"
          onPageChange={pagination.setPage}
          onPageSizeChange={pagination.setPageSize}
        />
      </div>

      <div className="mt-4 flex justify-end">
        <Button
          icon={<RotateCcw className="h-4 w-4" />}
          loading={regenerating}
          disabled={saved.size === 0}
          onClick={() => void onRegenerate()}
        >
          Regenerate with updated mappings
        </Button>
      </div>
    </div>
  )
}

export default function CreditorsAgeing() {
  const [activeTab, setActiveTab] = useState<'generate' | 'mappings'>('generate')
  const [file, setFile] = useState<File | null>(null)
  const [asOnDate, setAsOnDate] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ReportResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [mappings, setMappings] = useState<MappingRow[]>([])
  const [archivedMappings, setArchivedMappings] = useState<MappingRow[]>([])
  const [mappingsLoading, setMappingsLoading] = useState(true)
  const [archivedLoading, setArchivedLoading] = useState(false)
  const [mappingError, setMappingError] = useState<string | null>(null)

  const loadMappings = useCallback(async () => {
    setMappingsLoading(true)
    setMappingError(null)
    try {
      setMappings(await get<MappingRow[]>(`${BASE}/mappings`))
    } catch (err) {
      setMappingError(err instanceof ApiError ? err.message : 'Could not load vendor mappings.')
    } finally {
      setMappingsLoading(false)
    }
  }, [])

  const loadArchivedMappings = useCallback(async () => {
    setArchivedLoading(true)
    setMappingError(null)
    try {
      setArchivedMappings(await get<MappingRow[]>(`${BASE}/mappings/archived`))
    } catch (err) {
      setMappingError(err instanceof ApiError ? err.message : 'Could not load archived mappings.')
    } finally {
      setArchivedLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadMappings()
  }, [loadMappings])

  async function generate() {
    if (!file) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const form = new FormData()
      form.append('file', file)
      if (asOnDate) form.append('as_on_date', asOnDate)
      const response = await postForm<{ job_id: string }>(`${BASE}/process`, form)
      setJobId(response.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the report generation job.')
    } finally {
      setSubmitting(false)
    }
  }

  function downloadReport() {
    if (!jobId) return
    const anchor = document.createElement('a')
    anchor.href = apiUrl(`${BASE}/download/${jobId}`)
    anchor.download = result?.download_filename ?? 'Ultrafine Creditors Ageing.xlsx'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }

  function reset() {
    setFile(null)
    setAsOnDate('')
    setJobId(null)
    setResult(null)
    setError(null)
  }

  const active = jobId !== null && result === null && error === null

  return (
    <AppShell title="Ultrafine Creditors Ageing Report Generator">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl"><FileSpreadsheet className="h-5 w-5" /></span>
            <div>
              <p className="text-sm font-semibold text-accent">Ultrafine payables intelligence</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Build a complete creditors ageing workbook</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">Transform a fresh Tally TB export into Only Creditors, Advances and Intercompany schedules using the shared vendor classification database.</p>
            </div>
          </div>

          <div className="segmented-control self-start">
            <button
              type="button"
              onClick={() => setActiveTab('generate')}
              className={cn('rounded-xl px-4 py-2 text-sm font-semibold transition', activeTab === 'generate' ? 'bg-accent text-white' : 'text-ink-dim hover:bg-surface/70 hover:text-ink')}
            >
              Generate report
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('mappings')}
              className={cn('rounded-xl px-4 py-2 text-sm font-semibold transition', activeTab === 'mappings' ? 'bg-accent text-white' : 'text-ink-dim hover:bg-surface/70 hover:text-ink')}
            >
              Vendor mappings
            </button>
          </div>
        </GlassCard>

        {activeTab === 'generate' ? (
          <>
            <GlassCard padding="lg" className="flex flex-col gap-6">
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
                <div className="flex flex-col gap-2">
                  <span className="text-sm font-medium text-ink-dim">Tally TB export</span>
                  <FileDropzone
                    accept=".xlsx,.xlsm"
                    label="Drag and drop the Tally export here, or click to browse"
                    hint="The TB and Bill Wise sheets are detected from their headers"
                    files={file ? [file] : []}
                    onFilesSelected={(files) => setFile(files[0] ?? null)}
                    onRemove={() => setFile(null)}
                  />
                </div>
                <label className="flex flex-col gap-2">
                  <span className="text-sm font-medium text-ink-dim">Report as-on date (optional)</span>
                  <DatePicker value={asOnDate} onValueChange={setAsOnDate} aria-label="Report as-on date" />
                  <span className="text-xs leading-5 text-ink-faint">Leave blank to detect the date from the Tally period header. Ageing is calculated through the previous day.</span>
                </label>
              </div>

              {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{error}</p>}

              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={active || (!file && !result)} onClick={reset}>Reset</Button>
                <Button loading={submitting} disabled={!file || active} onClick={() => void generate()}>Generate creditors ageing report</Button>
              </div>
            </GlassCard>

            {jobId && (
              <GlassCard padding="lg" className="flex flex-col gap-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h3 className="font-display text-lg font-semibold text-ink">Report progress</h3>
                  {result && <Button icon={<Download className="h-4 w-4" />} onClick={downloadReport}>Save / download workbook</Button>}
                </div>
                <ProgressPanel
                  jobId={jobId}
                  poller={pollJob}
                  onDone={(next) => setResult(next ?? null)}
                  onError={setError}
                  onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
                />

                {result && (
                  <>
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                      {[
                        ['TB ledgers', result.tb_ledgers, 'text-ink'],
                        ['Bill Wise rows', result.bill_wise_rows, 'text-accent'],
                        ['Only Creditors', result.counts.only_creditors, 'text-emerald-500'],
                        ['Advances', result.counts.advances, 'text-amber-500'],
                        ['Intercompany', result.counts.intercompany, 'text-sky-500'],
                      ].map(([label, value, color]) => (
                        <div key={String(label)} className="subpanel px-4 py-3">
                          <p className="text-xs font-medium text-ink-faint">{label}</p>
                          <p className={`mt-1 font-display text-2xl font-semibold ${color}`}>{formatIndianNumber(Number(value))}</p>
                        </div>
                      ))}
                    </div>
                    <div className="subpanel flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                      <span className="text-ink-dim">Report date</span>
                      <strong className="text-ink">As on {result.as_on_label}</strong>
                    </div>

                    {result.new_vendors.length > 0 ? (
                      <NewVendorClassification
                        result={result}
                        mappings={mappings}
                        jobId={jobId}
                        onMappingsChanged={loadMappings}
                        onRegenerate={generate}
                        regenerating={submitting}
                      />
                    ) : (
                      <div className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600 dark:text-emerald-300">
                        <CheckCircle2 className="h-4 w-4" />
                        Every vendor was classified from the centralized mapping database.
                      </div>
                    )}

                    {result.log.length > 0 && (
                      <div className="flex flex-col gap-2">
                        <h4 className="flex items-center gap-2 text-sm font-semibold text-ink"><Terminal className="h-4 w-4 text-accent" />Processing log</h4>
                        <div className="flex max-h-72 flex-col gap-0.5 overflow-auto rounded-xl border border-slate-700 bg-slate-950 p-4 font-mono text-xs leading-6">
                          {result.log.map(([level, message], index) => (
                            <div key={`${index}-${level}`} className="flex gap-2">
                              <span className={cn('shrink-0 font-semibold uppercase', logColor(level))}>[{level}]</span>
                              <span className="text-slate-300">{message}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </GlassCard>
            )}
          </>
        ) : (
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex items-start gap-3">
              <MapPinned className="mt-0.5 h-5 w-5 text-accent" />
              <div>
                <h2 className="font-display text-lg font-semibold text-ink">Centralized vendor classifications</h2>
                <p className="mt-1 text-sm leading-6 text-ink-dim">The existing desktop mappings are already included. Search, add, edit, archive or restore vendors here; every user and report run sees the same current mapping.</p>
              </div>
            </div>
            <MappingManager
              rows={mappings}
              archivedRows={archivedMappings}
              loading={mappingsLoading}
              archivedLoading={archivedLoading}
              error={mappingError}
              onReload={loadMappings}
              onLoadArchived={loadArchivedMappings}
            />
          </GlassCard>
        )}
      </div>
    </AppShell>
  )
}
