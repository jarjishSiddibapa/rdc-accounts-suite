import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  ListChecks,
  RefreshCw,
  Scale,
  Trash2,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { Pagination } from '@/components/Pagination'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/trial-balance'

// ── report generation ────────────────────────────────────────────────────

interface AccountOption {
  account_code: string
  description: string
}

interface AccountsResponse {
  token: string
  raw_row_count: number
  accounts: AccountOption[]
}

interface ProcessResult {
  output_path: string
  download_filename: string
  missing_codes: string[]
  missing_account_ho: string[]
  row_count: number
  raw_row_count: number
  matched_count: number
  unmatched_count: number
  log: string[]
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

// ── missing-mapping fix flow (Location Code -> Location Name + Region) ──

function MissingCodesFix({
  codes,
  knownRegions,
  onRegenerate,
  regenerating,
}: {
  codes: string[]
  knownRegions: string[]
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [forms, setForms] = useState<Record<string, { location_name: string; region: string }>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(codes, 10)

  async function handleFix(code: string) {
    const form = forms[code]
    const locationName = form?.location_name?.trim()
    const region = form?.region?.trim()
    if (!locationName || !region) return
    setFixing(code)
    setError(null)
    try {
      await post(`${BASE}/mappings/fix`, { location_code: code, location_name: locationName, region })
      setFixed((prev) => ({ ...prev, [code]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = codes.length > 0 && codes.every((c) => fixed[c])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing mappings ({formatIndianNumber(codes.length)} location code{codes.length === 1 ? '' : 's'})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        These Location Codes had no Location Name / Region mapped. Enter both for each, then
        regenerate the report. Accounts Incharge is filled in automatically from the Region
        Incharge table.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <datalist id="trial-balance-known-regions">
        {knownRegions.map((r) => (
          <option key={r} value={r} />
        ))}
      </datalist>
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((code) => {
          const form = forms[code] ?? { location_name: '', region: '' }
          const isFixed = Boolean(fixed[code])
          return (
            <div
              key={code}
              className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[8rem]">
                {code}
              </span>
              <input
                placeholder="Location Name"
                value={form.location_name}
                disabled={isFixed}
                onChange={(e) =>
                  setForms((prev) => ({
                    ...prev,
                    [code]: { ...form, location_name: e.target.value },
                  }))
                }
                className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-48"
              />
              <input
                list="trial-balance-known-regions"
                placeholder="Region"
                value={form.region}
                disabled={isFixed}
                onChange={(e) =>
                  setForms((prev) => ({
                    ...prev,
                    [code]: { ...form, region: e.target.value },
                  }))
                }
                className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-36"
              />
              {isFixed ? (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" /> Saved
                </span>
              ) : (
                <Button
                  variant="secondary"
                  loading={fixing === code}
                  disabled={!form.location_name.trim() || !form.region.trim()}
                  onClick={() => void handleFix(code)}
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
        itemLabel="unmapped location codes"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
      <div className="flex justify-stretch sm:justify-end">
        <Button
          variant="primary"
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

// ── missing-mapping fix flow (Account Code -> Head Office Assigned Person) ──

function MissingAccountHoFix({
  codes,
  knownHoPersons,
  onRegenerate,
  regenerating,
}: {
  codes: string[]
  knownHoPersons: string[]
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [forms, setForms] = useState<Record<string, string>>({})
  const [fixed, setFixed] = useState<Record<string, boolean>>({})
  const [fixing, setFixing] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pagination = usePagination(codes, 10)

  async function handleFix(code: string) {
    const person = forms[code]?.trim()
    if (!person) return
    setFixing(code)
    setError(null)
    try {
      await post(`${BASE}/mappings/account-ho`, { account_code: code, ho_person: person })
      setFixed((prev) => ({ ...prev, [code]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save mapping fix.')
    } finally {
      setFixing(null)
    }
  }

  const allFixed = codes.length > 0 && codes.every((c) => fixed[c])

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Missing mappings ({formatIndianNumber(codes.length)} account code{codes.length === 1 ? '' : 's'})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        These Account Codes had no Head Office Assigned Person mapped — the column was left
        blank in the output. Enter a name for each, then regenerate the report.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <datalist id="trial-balance-known-ho-persons">
        {knownHoPersons.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
      <div className="flex flex-col gap-2">
        {pagination.pagedItems.map((code) => (
          <div
            key={code}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[8rem]">
              {code}
            </span>
            <input
              list="trial-balance-known-ho-persons"
              placeholder="Head Office Assigned Person"
              value={forms[code] ?? ''}
              disabled={fixed[code]}
              onChange={(e) => setForms((prev) => ({ ...prev, [code]: e.target.value }))}
              className="field-control w-full py-1.5 text-sm disabled:opacity-50 sm:min-h-9 sm:w-56"
            />
            {fixed[code] ? (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            ) : (
              <Button
                variant="secondary"
                loading={fixing === code}
                disabled={!forms[code]?.trim()}
                onClick={() => void handleFix(code)}
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
        itemLabel="unmapped account codes"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
      <div className="flex justify-stretch sm:justify-end">
        <Button
          variant="primary"
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
    key: 'location-codes',
    title: 'Location Codes',
    addLabel: 'Add location code',
    columns: [
      { key: 'location_code', label: 'Location Code' },
      { key: 'location_name', label: 'Location Name' },
    ],
    buildKey: (row) => encodeURIComponent(row.location_code ?? ''),
    buildBody: (row) => ({
      location_code: row.location_code ?? '',
      location_name: row.location_name ?? '',
    }),
  },
  {
    key: 'location-region',
    title: 'Location Region Map',
    addLabel: 'Add location region',
    columns: [
      { key: 'location_name', label: 'Location Name' },
      { key: 'region', label: 'Region' },
    ],
    buildKey: (row) => encodeURIComponent(row.location_name ?? ''),
    buildBody: (row) => ({
      location_name: row.location_name ?? '',
      region: row.region ?? '',
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
    key: 'account-ho',
    title: 'Account HO Map',
    addLabel: 'Add account HO mapping',
    columns: [
      { key: 'account_code', label: 'Account Code' },
      { key: 'ho_person', label: 'HO Person' },
    ],
    buildKey: (row) => encodeURIComponent(row.account_code ?? ''),
    buildBody: (row) => ({
      account_code: row.account_code ?? '',
      ho_person: row.ho_person ?? '',
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

export default function TrialBalance() {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const [token, setToken] = useState<string | null>(null)
  const [rawRowCount, setRawRowCount] = useState(0)
  const [accounts, setAccounts] = useState<AccountOption[]>([])
  const [selected, setSelected] = useState<Record<string, boolean>>({})

  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [processError, setProcessError] = useState<string | null>(null)
  const [activityLog, setActivityLog] = useState<string[]>([])

  const [knownRegions, setKnownRegions] = useState<string[]>([])
  const [knownHoPersons, setKnownHoPersons] = useState<string[]>([])

  const [activeMappingTab, setActiveMappingTab] = useState(0)

  const accountPagination = usePagination(accounts, 10, token)
  const selectedCount = Object.values(selected).filter(Boolean).length

  useEffect(() => {
    void get<MappingRow[]>(`${BASE}/mappings/region-incharge`)
      .then((rows) => setKnownRegions(rows.map((r) => r.region).filter(Boolean)))
      .catch(() => {
        // Non-critical: the region input just falls back to free text.
      })
    void get<MappingRow[]>(`${BASE}/mappings/account-ho`)
      .then((rows) => setKnownHoPersons([...new Set(rows.map((r) => r.ho_person).filter(Boolean))]))
      .catch(() => {
        // Non-critical: the HO person input just falls back to free text.
      })
  }, [])

  async function handleFileSelected(files: File[]) {
    const picked = files[0] ?? null
    setFile(picked)
    setToken(null)
    setRawRowCount(0)
    setAccounts([])
    setSelected({})
    setResult(null)
    setJobId(null)
    setProcessError(null)
    setActivityLog([])
    if (!picked) return
    setUploading(true)
    setUploadError(null)
    try {
      const fd = new FormData()
      fd.append('file', picked)
      const res = await postForm<AccountsResponse>(`${BASE}/accounts`, fd)
      setToken(res.token)
      setRawRowCount(res.raw_row_count)
      setAccounts(res.accounts)
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'Failed to parse the uploaded file.')
    } finally {
      setUploading(false)
    }
  }

  function selectAllAccounts() {
    setSelected(Object.fromEntries(accounts.map((a) => [a.account_code, true])))
  }

  function clearSelectedAccounts() {
    setSelected({})
  }

  async function handleProcess() {
    if (!token) return
    setSubmitting(true)
    setProcessError(null)
    setResult(null)
    setJobId(null)
    setActivityLog(['[INFO] Queuing report for processing...'])
    try {
      const accountCodes = Object.entries(selected)
        .filter(([, checked]) => checked)
        .map(([code]) => code)
      const res = await post<{ job_id: string }>(`${BASE}/process`, {
        token,
        account_codes: accountCodes,
      })
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
    a.download = result?.download_filename || 'Location_Report.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  const missingCodes = result?.missing_codes ?? []
  const missingAccountHo = result?.missing_account_ho ?? []

  return (
    <AppShell title="Trial Balance — Location-wise Report">
      <div className="flex flex-col gap-6">
        {/* ── Report generation ──────────────────────────────────────── */}
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <Scale className="h-5 w-5" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">
                Ledger intelligence
              </p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Generate location-wise report
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload the Detail Trial Balance export, pick the accounts to pivot on, then
                generate the report.
              </p>
            </div>
          </div>

          <FileDropzone
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop the Detail Trial Balance export here, or click to browse"
            files={file ? [file] : []}
            onFilesSelected={(f) => void handleFileSelected(f)}
            onRemove={() => void handleFileSelected([])}
          />

          {uploading && <p className="text-sm text-ink-dim">Parsing accounts...</p>}
          {uploadError && (
            <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {uploadError}
            </p>
          )}

          {token && (
            <div className="flex flex-col gap-3 rounded-xl border border-border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-sm font-medium text-ink-dim">
                  <ListChecks className="h-4 w-4 text-accent" />
                  {formatIndianNumber(rawRowCount)} rows parsed —{' '}
                  {formatIndianNumber(accounts.length)} distinct account
                  {accounts.length === 1 ? '' : 's'} found. Select which to pivot on
                  (leave none selected for an unfiltered report).
                </span>
                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={selectAllAccounts}
                    className="text-xs font-semibold text-accent hover:text-accent-2"
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    onClick={clearSelectedAccounts}
                    className="text-xs font-semibold text-ink-faint hover:text-ink"
                  >
                    Clear
                  </button>
                </div>
              </div>

              {accounts.length > 0 && (
                <>
                  <div className="flex flex-col gap-1">
                    {accountPagination.pagedItems.map((account) => (
                      <label
                        key={account.account_code}
                        className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm text-ink hover:bg-bg-soft/60"
                      >
                        <input
                          type="checkbox"
                          checked={Boolean(selected[account.account_code])}
                          onChange={(e) =>
                            setSelected((prev) => ({
                              ...prev,
                              [account.account_code]: e.target.checked,
                            }))
                          }
                          className="h-4 w-4 accent-accent"
                        />
                        <span className="shrink-0 font-mono text-xs text-ink-faint">
                          {account.account_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{account.description}</span>
                      </label>
                    ))}
                  </div>
                  <Pagination
                    page={accountPagination.page}
                    pageCount={accountPagination.pageCount}
                    pageSize={accountPagination.pageSize}
                    totalItems={accountPagination.totalItems}
                    itemLabel="accounts"
                    onPageChange={accountPagination.setPage}
                    onPageSizeChange={accountPagination.setPageSize}
                  />
                </>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-ink-faint">
                  {selectedCount === 0
                    ? 'No accounts selected — report will be unfiltered.'
                    : `${formatIndianNumber(selectedCount)} account${selectedCount === 1 ? '' : 's'} selected.`}
                </span>
                <Button onClick={() => void handleProcess()} loading={submitting} disabled={!token}>
                  Process
                </Button>
              </div>
            </div>
          )}

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
                  ['Matched', result.matched_count, 'text-emerald-500'],
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
                  <span className="text-ink-faint">Output file</span>
                  <p className="text-ink">{result.download_filename}</p>
                </div>
                <Button icon={<FileSpreadsheet className="h-4 w-4" />} onClick={handleDownloadReport}>
                  Save / download report
                </Button>
              </div>

              {missingCodes.length > 0 && (
                <MissingCodesFix
                  codes={missingCodes}
                  knownRegions={knownRegions}
                  regenerating={submitting}
                  onRegenerate={() => void handleProcess()}
                />
              )}

              {missingAccountHo.length > 0 && (
                <MissingAccountHoFix
                  codes={missingAccountHo}
                  knownHoPersons={knownHoPersons}
                  regenerating={submitting}
                  onRegenerate={() => void handleProcess()}
                />
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
