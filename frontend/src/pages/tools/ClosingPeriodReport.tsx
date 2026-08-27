import { useState } from 'react'
import { CheckCircle2, Download, PackageCheck, RotateCcw, Terminal, XCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, apiUrl, get, post, postForm } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { cn } from '@/utils/cn'

const BASE = '/tools/closing-period'

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
  skipped: number
  total_rows: number
  date_label: string
  output_path: string
  download_filename: string
  log: [string, string][]
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

export default function ClosingPeriodReport() {
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<CombineResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const active = jobId !== null && result === null && !error

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
    a.download = result?.download_filename || 'closing_period_report.xlsx'
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
    <AppShell title="Closing Period Report Generator">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl"><PackageCheck className="h-5 w-5" /></span>
            <div>
              <p className="text-sm font-semibold text-accent">Inventory reporting</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Combine closing period reports</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">Upload Oracle BI Publisher closing-period inventory reports (saved as .xls) from every location. Rows are filtered to MOD-RM, NMOD-RM, and STORES sub-inventories and combined into one workbook with a location/sub-inventory summary.</p>
            </div>
          </div>

          <FileDropzone
            multiple
            accept=".xls"
            label="Drag & drop closing period .xls files here, or click to browse"
            hint="Oracle BI Publisher HTML-in-.xls exports, one per location"
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
              onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
            />

            {result && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-ink">{formatIndianNumber(result.files)}</p>
                    <p className="text-xs text-ink-faint">Files processed</p>
                  </div>
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-slate-400">{formatIndianNumber(result.skipped)}</p>
                    <p className="text-xs text-ink-faint">Skipped</p>
                  </div>
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-emerald-500">{formatIndianNumber(result.total_rows)}</p>
                    <p className="text-xs text-ink-faint">Data rows</p>
                  </div>
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-ink">{result.date_label}</p>
                    <p className="text-xs text-ink-faint">Period detected</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" />
                  Combined workbook ready for download.
                </div>
              </div>
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
      </div>
    </AppShell>
  )
}
