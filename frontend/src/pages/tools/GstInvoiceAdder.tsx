import { useState } from 'react'
import { CheckCircle2, Download, FileCheck2, RotateCcw, Terminal, XCircle } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, apiUrl, get, post, postForm } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'

const BASE = '/tools/gst-invoice-adder'

interface EnrichResult {
  total: number
  found: number
  blank: number
  output_path: string
  download_name: string
  log: [string, string][]
}

interface EnrichJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: EnrichResult | null
  error: string | null
}

async function pollEnrichJob(jobId: string): Promise<JobState<EnrichResult>> {
  const job = await get<EnrichJobResponse>(`${BASE}/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result ?? undefined,
    error: job.error ?? undefined,
  }
}

export default function GstInvoiceAdder() {
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<EnrichResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const active = jobId !== null && result === null && !error

  async function handleGenerate() {
    if (!file) return
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await postForm<{ job_id: string }>(`${BASE}/process`, formData)
      setJobId(res.job_id)
      // submitting stays true until the background job itself settles
      // (cleared in ProgressPanel's onDone/onError below) - the request
      // returning just means the job was queued, not that it's finished.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start processing.')
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = result?.download_name || 'gst_enriched.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  function resetAll() {
    if (active) return
    setFile(null)
    setJobId(null)
    setResult(null)
    setError(null)
  }

  return (
    <AppShell title="GST Invoice Number Adder">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <FileCheck2 className="h-5 w-5" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Data enrichment</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Add GST Invoice Numbers
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Upload an RDC Receivable Aging Report and every row is looked up in Oracle by invoice
                number and date, then a new "GST Invoice Number" column is inserted next to it.
              </p>
            </div>
          </div>

          <FileDropzone
            accept=".xlsx,.xlsb,.xls"
            label="Drag & drop the Receivable Aging Report here, or click to browse"
            hint="Supports .xlsx, .xlsb, and .xls"
            files={file ? [file] : []}
            onFilesSelected={(incoming) => setFile(incoming[0] ?? null)}
            onRemove={() => setFile(null)}
          />

          {error && (
            <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
              {error}
            </p>
          )}

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={active || (!file && !result)} onClick={resetAll}>
              Reset
            </Button>
            <Button onClick={() => void handleGenerate()} loading={submitting} disabled={!file || active}>
              Fetch GST numbers &amp; save
            </Button>
          </div>
        </GlassCard>

        {jobId && (
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="font-display text-lg font-semibold text-ink">Enrichment progress</h3>
              {result && (
                <Button icon={<Download className="h-4 w-4" />} onClick={handleDownload}>
                  Download enriched report
                </Button>
              )}
            </div>

            <ProgressPanel
              jobId={jobId}
              poller={pollEnrichJob}
              onDone={(next) => {
                setResult(next ?? null)
                setSubmitting(false)
              }}
              onError={(message) => {
                setError(message)
                setSubmitting(false)
              }}
              onCancel={() => post(`${BASE}/jobs/${jobId}/cancel`)}
            />

            {result && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-ink">{formatIndianNumber(result.total)}</p>
                    <p className="text-xs text-ink-faint">Total rows</p>
                  </div>
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-emerald-500">{formatIndianNumber(result.found)}</p>
                    <p className="text-xs text-ink-faint">GST found</p>
                  </div>
                  <div className="subpanel p-3 text-center">
                    <p className="text-xl font-bold text-amber-500">{formatIndianNumber(result.blank)}</p>
                    <p className="text-xs text-ink-faint">Blank / no GST</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" />
                  Enriched report ready for download.
                </div>
              </div>
            )}

            {result?.log && result.log.length > 0 && (
              <div className="flex flex-col gap-2">
                <h4 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <Terminal className="h-4 w-4 text-accent" />
                  Processing log
                </h4>
                <pre className="max-h-72 overflow-auto rounded-xl border border-slate-700 bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-300">
                  {result.log.map(([tag, message]) => `[${tag}] ${message}`).join('\n')}
                </pre>
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
