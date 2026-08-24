import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Combine, Download, RefreshCw, RotateCcw, Terminal, XCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { MappingTable, type MappingColumn, type MappingRow } from '@/components/MappingTable'
import { ApiError, apiUrl, del, get, post, postForm, put } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/gstr2b'

function logLevelColor(tag: string): string {
  switch (tag.toLowerCase()) {
    case 'success':
      return 'text-emerald-400'
    case 'warn':
    case 'warning':
      return 'text-amber-400'
    case 'error':
      return 'text-red-400'
    case 'dim':
      return 'text-slate-500'
    default:
      return 'text-sky-400'
  }
}

interface CombineResult {
  files: number
  counts: Record<string, number>
  output_path: string
  download_filename: string
  log: [string, string][]
  unresolved_state_codes: number[]
}

// ── missing-mapping fix flow (State Code -> State Name) ─────────────────

function UnresolvedStateCodesFix({
  codes,
  knownStateNames,
  onRegenerate,
  regenerating,
}: {
  codes: number[]
  knownStateNames: string[]
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [forms, setForms] = useState<Record<number, string>>({})
  const [fixed, setFixed] = useState<Record<number, boolean>>({})
  const [fixing, setFixing] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleFix(code: number) {
    const name = forms[code]?.trim()
    if (!name) return
    setFixing(code)
    setError(null)
    try {
      await post(`${BASE}/mappings/state-codes`, { code, name })
      setFixed((prev) => ({ ...prev, [code]: true }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save state code mapping.')
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
          Missing mappings ({formatIndianNumber(codes.length)} state code{codes.length === 1 ? '' : 's'})
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        These GST state codes weren't in the State Codes table — the output workbook used a
        placeholder like &quot;Unknown state (NN)&quot; instead. Enter the real state name for
        each, then combine again.
      </p>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <datalist id="gstr2b-known-state-names">
        {knownStateNames.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <div className="flex flex-col gap-2">
        {codes.map((code) => (
          <div
            key={code}
            className="subpanel flex flex-col items-stretch gap-2 px-3 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:py-2"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink sm:min-w-[6rem]">
              Code {code}
            </span>
            <input
              list="gstr2b-known-state-names"
              placeholder="State Name"
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
      <div className="flex justify-stretch sm:justify-end">
        <Button
          icon={<RefreshCw className="h-4 w-4" />}
          disabled={!allFixed}
          loading={regenerating}
          onClick={onRegenerate}
        >
          Combine again
        </Button>
      </div>
    </div>
  )
}

interface CombineJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: CombineResult | null
  error: string | null
}

async function pollCombineJob(jobId: string): Promise<JobState<CombineResult>> {
  const job = await get<CombineJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

const STATE_CODE_COLUMNS: MappingColumn[] = [
  { key: 'code', label: 'Code' },
  { key: 'name', label: 'State Name' },
]

function StateCodeMappingSection() {
  const [rows, setRows] = useState<MappingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await get<{ code: number; name: string }[]>(`${BASE}/mappings/state-codes`)
      setRows(data.map((row) => ({ code: String(row.code), name: row.name })))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load state code mappings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function handleAdd(row: MappingRow) {
    await post(`${BASE}/mappings/state-codes`, { code: Number(row.code), name: row.name ?? '' })
    await load()
  }

  async function handleEdit(index: number, row: MappingRow) {
    const original = rows[index]
    await put(`${BASE}/mappings/state-codes/${original.code}`, { code: Number(row.code), name: row.name ?? '' })
    await load()
  }

  async function handleDelete(index: number) {
    const original = rows[index]
    await del(`${BASE}/mappings/state-codes/${original.code}`)
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
          title="State Codes"
          addLabel="Add state code"
          columns={STATE_CODE_COLUMNS}
          rows={rows}
          onAdd={handleAdd}
          onEdit={handleEdit}
          onDelete={handleDelete}
        />
      )}
    </div>
  )
}

export default function Gstr2bCombinator() {
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<CombineResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [knownStateNames, setKnownStateNames] = useState<string[]>([])

  const active = jobId !== null && result === null && !error

  useEffect(() => {
    void get<{ code: number; name: string }[]>(`${BASE}/mappings/state-codes`)
      .then((rows) => setKnownStateNames([...new Set(rows.map((r) => r.name).filter(Boolean))]))
      .catch(() => {
        // Non-critical: the state name input just falls back to free text.
      })
  }, [])

  async function handleCombine() {
    if (files.length === 0) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const formData = new FormData()
      files.forEach((file) => formData.append('files', file))
      const res = await postForm<{ job_id: string }>(`${BASE}/combine`, formData)
      setJobId(res.job_id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start the combine job.')
    } finally {
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = result?.download_filename || 'combined_gstr_2b_file.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  function resetAll() {
    if (active) return
    setFiles([])
    setJobId(null)
    setResult(null)
    setError(null)
  }

  return (
    <AppShell title="GSTR-2B File Combinator">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl"><Combine className="h-5 w-5" /></span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Data consolidation</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Combine GSTR-2B files</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">Upload GSTR-2B Excel exports from every state and combine their B2B, B2BA, B2B-CDNR, B2B-CDNRA and IMPG tabs into one workbook.</p>
            </div>
          </div>

          <FileDropzone
            multiple
            accept=".xlsx"
            label="Drag & drop GSTR-2B .xlsx files here, or click to browse"
            hint="Supports .xlsx exports named like 042026_06AACCU5797P1ZF_GSTR2B_20052026.xlsx"
            files={files}
            onFilesSelected={(incoming) => setFiles((previous) => [...previous, ...incoming])}
            onRemove={(index) => setFiles((previous) => previous.filter((_, current) => current !== index))}
          />

          {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>}

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={active || (files.length === 0 && !result)} onClick={resetAll}>Reset</Button>
            <Button onClick={() => void handleCombine()} loading={submitting} disabled={files.length === 0 || active}>
              Combine files{files.length > 0 ? ` (${formatIndianNumber(files.length)})` : ''}
            </Button>
          </div>
        </GlassCard>

        {jobId && (
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="font-display text-lg font-semibold text-ink">Combine progress</h3>
              {result && (
                <Button icon={<Download className="h-4 w-4" />} onClick={handleDownload}>
                  Download combined workbook
                </Button>
              )}
            </div>

            <ProgressPanel
              jobId={jobId}
              poller={pollCombineJob}
              onDone={(next) => setResult(next ?? null)}
              onError={(message) => setError(message)}
            />

            {result && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-ink">{formatIndianNumber(result.files)}</p>
                    <p className="text-xs text-ink-faint">Files processed</p>
                  </div>
                  {Object.entries(result.counts).map(([tab, count]) => (
                    <div key={tab} className="subpanel p-3 text-center">
                      <p className="text-xl font-bold text-emerald-500">{formatIndianNumber(count)}</p>
                      <p className="text-xs text-ink-faint">{tab}</p>
                    </div>
                  ))}
                </div>
                <div className="flex items-center gap-2 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" />
                  Combined workbook ready for download.
                </div>
              </div>
            )}

            {result && result.unresolved_state_codes.length > 0 && (
              <UnresolvedStateCodesFix
                codes={result.unresolved_state_codes}
                knownStateNames={knownStateNames}
                regenerating={submitting}
                onRegenerate={() => void handleCombine()}
              />
            )}

            {result?.log && result.log.length > 0 && (
              <div className="flex flex-col gap-2">
                <h4 className="flex items-center gap-2 text-sm font-semibold text-ink"><Terminal className="h-4 w-4 text-accent" />Processing log</h4>
                <div className="flex max-h-72 flex-col gap-0.5 overflow-auto rounded-xl border border-slate-700 bg-slate-950 p-4 font-mono text-xs leading-6">
                  {result.log.map(([tag, message], index) => (
                    <div key={`${index}-${tag}-${message}`} className="flex gap-2">
                      <span className={cn('shrink-0 font-semibold uppercase', logLevelColor(tag))}>[{tag}]</span>
                      <span className="text-slate-300">{message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {error && (
              <div className="flex items-center gap-2 text-sm text-red-500">
                <XCircle className="h-4 w-4" />
                {error}
              </div>
            )}
          </GlassCard>
        )}

        <GlassCard padding="lg" className="flex flex-col gap-5">
          <div>
            <h3 className="font-display text-lg font-semibold text-ink">State code mappings</h3>
            <p className="mt-1 text-sm text-ink-dim">Add, edit, or archive the GST state codes used to resolve each filename's State Name during combine.</p>
          </div>
          <StateCodeMappingSection />
        </GlassCard>
      </div>
    </AppShell>
  )
}
