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
import { FileDropzone } from '@/components/FileDropzone'
import { GlassCard } from '@/components/GlassCard'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { Pagination } from '@/components/Pagination'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { LoadingNotice } from '@/components/LoadingNotice'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/trial-balance-formatter'

const MAPPING_COLUMNS: MappingColumn[] = [
  { key: 'ledger_name', label: 'Ledger name' },
  { key: 'nature', label: 'TB nature' },
  { key: 'row_type', label: 'Row treatment' },
]

interface LedgerMapping {
  ledger_name: string
  nature: 'Dr' | 'Cr'
  is_subgroup: boolean
}

interface MissingLedger {
  name: string
  guessed_nature: 'Dr' | 'Cr'
  is_subgroup: boolean
}

interface ReportResult {
  output_path: string
  download_filename: string
  sheet_name: string
  as_on_label: string
  as_on_date: string
  row_count: number
  warnings: string[]
  needs_review: MissingLedger[]
  reference_adjustments_applied: boolean
  tb_balance: number
  log: [string, string][]
}

interface JobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: ReportResult | null
  error: string | null
}

function toMappingRow(row: LedgerMapping): MappingRow {
  return {
    ledger_name: row.ledger_name,
    nature: row.nature,
    row_type: row.is_subgroup ? 'Group total' : 'Ledger',
  }
}

function mappingPayload(row: MappingRow) {
  return {
    ledger_name: row.ledger_name ?? '',
    nature: (row.nature ?? '').trim().toLowerCase() === 'cr' ? 'Cr' : 'Dr',
    is_subgroup: (row.row_type ?? '').trim().toLowerCase() === 'group total',
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
    await put(`${BASE}/mappings/${encodeURIComponent(rows[index].ledger_name ?? '')}`, mappingPayload(row))
    await onReload()
  }

  async function archive(index: number) {
    await del(`${BASE}/mappings/${encodeURIComponent(rows[index].ledger_name ?? '')}`)
    await onReload()
  }

  async function restore(index: number) {
    await post(`${BASE}/mappings/${encodeURIComponent(archivedRows[index].ledger_name ?? '')}/restore`)
    await Promise.all([onReload(), onLoadArchived()])
  }

  if (loading) {
    return <LoadingNotice detail="Loading centralized ledger classifications." />
  }

  return (
    <div className="flex flex-col gap-4">
      {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{error}</p>}
      <MappingTable
        title="Ledger nature and row treatment"
        addLabel="Add ledger"
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
      <p className="text-xs leading-5 text-ink-faint">
        Use <strong className="text-ink-dim">Group total</strong> for orange subtotal rows that must stay blank in TB Balance. Primary groups are detected from Tally and highlighted yellow automatically.
      </p>
    </div>
  )
}

function MissingLedgerReview({
  rows,
  onSaved,
}: {
  rows: MissingLedger[]
  onSaved: () => Promise<void>
}) {
  const [drafts, setDrafts] = useState<Record<string, MissingLedger>>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(rows, 10, rows.map((row) => row.name).join('\u0000'))

  useEffect(() => {
    setDrafts(Object.fromEntries(rows.map((row) => [row.name, row])))
    setSaved(new Set())
  }, [rows])

  async function save(name: string) {
    const draft = drafts[name]
    if (!draft) return
    setSaving(name)
    setError(null)
    try {
      await post(`${BASE}/mappings`, {
        ledger_name: name,
        nature: draft.guessed_nature,
        is_subgroup: draft.is_subgroup,
      })
      setSaved((current) => new Set(current).add(name))
      await onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not save ${name}.`)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="rounded-2xl border border-amber-500/30 bg-amber-500/[0.07] p-4 sm:p-5">
      <div className="flex gap-3">
        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
        <div>
          <h3 className="font-display text-lg font-semibold text-ink">Review new ledger classifications</h3>
          <p className="mt-1 text-sm leading-6 text-ink-dim">The workbook is available now using clearly reported provisional classifications. Save the correct nature and row treatment so every later run uses the centralized decision.</p>
        </div>
      </div>
      {error && <p className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>}
      <div className="mt-5 flex flex-col gap-3">
        {pagination.pagedItems.map((row) => {
          const draft = drafts[row.name] ?? row
          const isSaved = saved.has(row.name)
          return (
            <div key={row.name} className="subpanel grid gap-3 p-4 lg:grid-cols-[minmax(15rem,1fr)_10rem_12rem_auto] lg:items-end">
              <div className="min-w-0">
                <p className="text-xs font-medium text-ink-faint">Ledger</p>
                <p className="mt-1 break-words text-sm font-semibold text-ink">{row.name}</p>
              </div>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium text-ink-dim">TB nature</span>
                <select
                  className="field-control"
                  value={draft.guessed_nature}
                  onChange={(event) => setDrafts((current) => ({ ...current, [row.name]: { ...draft, guessed_nature: event.target.value as 'Dr' | 'Cr' } }))}
                >
                  <option value="Dr">Debit</option>
                  <option value="Cr">Credit</option>
                </select>
              </label>
              <label className="flex min-h-11 items-center gap-2 rounded-xl border border-border bg-surface/55 px-3 text-sm text-ink-dim">
                <input
                  type="checkbox"
                  checked={draft.is_subgroup}
                  onChange={(event) => setDrafts((current) => ({ ...current, [row.name]: { ...draft, is_subgroup: event.target.checked } }))}
                  className="h-4 w-4 accent-[var(--color-accent)]"
                />
                Group total row
              </label>
              <Button
                variant={isSaved ? 'secondary' : 'primary'}
                loading={saving === row.name}
                disabled={isSaved}
                icon={isSaved ? <CheckCircle2 className="h-4 w-4" /> : undefined}
                onClick={() => void save(row.name)}
              >
                {isSaved ? 'Saved' : 'Save mapping'}
              </Button>
            </div>
          )
        })}
      </div>
      <Pagination
        className="mt-4"
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="new ledgers"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </div>
  )
}

export default function TrialBalanceFormatter() {
  const [activeTab, setActiveTab] = useState<'generate' | 'mappings'>('generate')
  const [file, setFile] = useState<File | null>(null)
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
      const rows = await get<LedgerMapping[]>(`${BASE}/mappings`)
      setMappings(rows.map(toMappingRow))
    } catch (err) {
      setMappingError(err instanceof ApiError ? err.message : 'Could not load ledger classifications.')
    } finally {
      setMappingsLoading(false)
    }
  }, [])

  const loadArchivedMappings = useCallback(async () => {
    setArchivedLoading(true)
    setMappingError(null)
    try {
      const rows = await get<LedgerMapping[]>(`${BASE}/mappings/archived`)
      setArchivedMappings(rows.map(toMappingRow))
    } catch (err) {
      setMappingError(err instanceof ApiError ? err.message : 'Could not load archived classifications.')
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
      const response = await postForm<{ job_id: string }>(`${BASE}/process`, form)
      setJobId(response.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the formatting job.')
    } finally {
      setSubmitting(false)
    }
  }

  function reset() {
    setFile(null)
    setJobId(null)
    setResult(null)
    setError(null)
  }

  function downloadReport() {
    if (!jobId) return
    const anchor = document.createElement('a')
    anchor.href = apiUrl(`${BASE}/download/${jobId}`)
    anchor.download = result?.download_filename ?? 'Ultrafine Trial Balance.xlsx'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }

  const active = jobId !== null && result === null && error === null
  const mappingSummary = useMemo(() => `${formatIndianNumber(mappings.length)} active ledger classifications`, [mappings.length])

  return (
    <AppShell title="Ultrafine Trial Balance Formatter">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 shrink-0 place-items-center rounded-xl"><FileSpreadsheet className="h-5 w-5" /></span>
            <div>
              <p className="text-sm font-semibold text-accent">Ultrafine financial reporting</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Turn a raw Tally trial balance into the approved Ultrafine format</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">Preserves the reference layout, colors, fonts, formulas, debit and credit signs, hierarchy highlights, column widths and date-based filename.</p>
            </div>
          </div>
          <div className="segmented-control self-start">
            <button type="button" onClick={() => setActiveTab('generate')} className={cn('rounded-xl px-4 py-2 text-sm font-semibold transition', activeTab === 'generate' ? 'bg-accent text-white' : 'text-ink-dim hover:bg-surface/70 hover:text-ink')}>Format trial balance</button>
            <button type="button" onClick={() => setActiveTab('mappings')} className={cn('rounded-xl px-4 py-2 text-sm font-semibold transition', activeTab === 'mappings' ? 'bg-accent text-white' : 'text-ink-dim hover:bg-surface/70 hover:text-ink')}>Ledger mappings</button>
          </div>
        </GlassCard>

        {activeTab === 'generate' ? (
          <>
            <GlassCard padding="lg" className="flex flex-col gap-6">
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium text-ink-dim">Raw Tally trial balance</span>
                <FileDropzone
                  accept=".xlsx,.xlsm"
                  label="Drag and drop the raw trial balance here, or click to browse"
                  hint="The reporting period and ledger hierarchy are read directly from the workbook"
                  files={file ? [file] : []}
                  onFilesSelected={(files) => setFile(files[0] ?? null)}
                  onRemove={() => setFile(null)}
                />
              </div>
              {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-500">{error}</p>}
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={active || (!file && !result)} onClick={reset}>Reset</Button>
                <Button loading={submitting} disabled={!file || active} onClick={() => void generate()}>Format trial balance</Button>
              </div>
            </GlassCard>

            {jobId && (
              <GlassCard padding="lg" className="flex flex-col gap-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h3 className="font-display text-lg font-semibold text-ink">Formatting progress</h3>
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
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      {[
                        ['Report date', `As on ${result.as_on_label}`],
                        ['Worksheet', result.sheet_name],
                        ['Ledger rows', formatIndianNumber(result.row_count)],
                        ['TB Balance', formatIndianNumber(result.tb_balance, { maximumFractionDigits: 2 })],
                      ].map(([label, value]) => (
                        <div key={label} className="subpanel px-4 py-3">
                          <p className="text-xs font-medium text-ink-faint">{label}</p>
                          <p className="mt-1 text-sm font-semibold text-ink">{value}</p>
                        </div>
                      ))}
                    </div>

                    {result.warnings.map((warning) => (
                      <p key={warning} className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">{warning}</p>
                    ))}

                    {result.needs_review.length > 0 ? (
                      <MissingLedgerReview rows={result.needs_review} onSaved={loadMappings} />
                    ) : (
                      <div className="flex items-center gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-600 dark:text-emerald-300">
                        <CheckCircle2 className="h-4 w-4" />
                        Every ledger used the centralized reference classification.
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
                <h2 className="font-display text-lg font-semibold text-ink">Centralized ledger classifications</h2>
                <p className="mt-1 text-sm leading-6 text-ink-dim">All classifications observed in the supplied reference workbook are preloaded. Search, add, edit, archive or restore them here; every user and worker sees the same current mapping.</p>
                <p className="mt-1 text-xs font-medium text-ink-faint">{mappingSummary}</p>
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
