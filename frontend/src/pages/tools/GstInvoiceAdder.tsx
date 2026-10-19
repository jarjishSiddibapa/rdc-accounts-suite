import { useState } from 'react'
import { CheckCircle2, Download, FileCheck2, RotateCcw, Terminal } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { ApiError, apiUrl, get, post } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'
import { AutoMailCard } from '@/components/AutoMailCard'
import { ReportMailPanel } from '@/components/ReportMailPanel'
import { useAuth } from '@/lib/auth-context'

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
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [result, setResult] = useState<EnrichResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const active = jobId !== null && result === null && !error

  async function handleGenerate() {
    setSubmitting(true)
    setError(null)
    setResult(null)
    setJobId(null)
    try {
      const res = await post<{ job_id: string }>(`${BASE}/process`)
      setJobId(res.job_id)
      // submitting stays true until the background job itself settles
      // (cleared in ProgressPanel's onDone/onError below) - the request
      // returning just means the job was queued, not that it's finished.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start processing.')
      setSubmitting(false)
    }
  }

  function handleDownload() {
    if (!jobId) return
    const a = document.createElement('a')
    a.href = apiUrl(`${BASE}/download/${jobId}`)
    a.download = result?.download_name || 'GST_Invoice_Numbers.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  function resetAll() {
    if (active) return
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
              <p className="text-sm font-semibold text-accent">Data enrichment</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Add GST Invoice Numbers
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Fetches the latest receivable ageing from the ERP, looks up every row in Oracle by
                invoice number and date, and adds a "GST Invoice Number" column next to it.
              </p>
            </div>
          </div>

          {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}

          <p className="text-xs text-ink-faint sm:text-right">
            Looks up each row's GST invoice number in Oracle before saving the enriched file.
          </p>

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={active || !result} onClick={resetAll}>
              Reset
            </Button>
            <Button onClick={() => void handleGenerate()} loading={submitting} disabled={active}>
              Save GST numbers
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

            {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
          </GlassCard>
        )}

        <ReportMailPanel baseUrl={BASE} reportName="GST Invoice Number report" />

        {isAdmin && <AutoMailCard baseUrl={BASE} reportName="GST Invoice Number report" />}
      </div>
    </AppShell>
  )
}
