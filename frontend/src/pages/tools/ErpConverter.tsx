import { useMemo, useRef, useState, type InputHTMLAttributes } from 'react'
import {
  Ban,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FolderPlus,
  ListRestart,
  RotateCcw,
  Terminal,
  Trash2,
  XCircle,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { FileDropzone } from '@/components/FileDropzone'
import { ProgressPanel, type JobState, type JobStatus } from '@/components/ProgressPanel'
import { Pagination } from '@/components/Pagination'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, get, post, postBlob, postForm } from '@/lib/api'
import { formatIndianNumber, formatIndianTime } from '@/lib/regional'

interface ConvertJobEntry {
  filename: string
  job_id: string
}

interface ConvertJobResponse {
  status: JobStatus
  progress: number
  phase: string
  result: 'html' | 'xlsx' | 'xls-binary' | null
  error: string | null
}

async function pollConvertJob(jobId: string): Promise<JobState<string | null>> {
  const job = await get<ConvertJobResponse>(`/tools/erp-to-excel/jobs/${jobId}`)
  return {
    status: job.status,
    progress: (job.progress ?? 0) * 100,
    phase: job.phase,
    result: job.result,
    error: job.error ?? undefined,
  }
}

function outputName(filename: string) {
  return filename.replace(/\.[^./]+$/, '.xlsx')
}

function fileIdentity(file: File) {
  return `${file.name}|${file.size}|${file.lastModified}`
}

export default function ErpConverter() {
  const [files, setFiles] = useState<File[]>([])
  const [jobs, setJobs] = useState<ConvertJobEntry[]>([])
  const [jobStates, setJobStates] = useState<Record<string, JobState<string | null>>>({})
  const [converting, setConverting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [logs, setLogs] = useState<string[]>([])
  const [savingAll, setSavingAll] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const lastEventRef = useRef<Record<string, string>>({})
  const jobPagination = usePagination(jobs, 5)

  const stats = useMemo(() => {
    const states = Object.values(jobStates)
    return {
      total: jobs.length,
      running: states.filter((state) => state.status === 'queued' || state.status === 'running').length,
      completed: states.filter((state) => state.status === 'done').length,
      failed: states.filter((state) => state.status === 'error').length,
      cancelled: states.filter((state) => state.status === 'cancelled').length,
    }
  }, [jobStates, jobs.length])

  const anyActive = jobs.some((job) => {
    const status = jobStates[job.job_id]?.status
    return !status || status === 'queued' || status === 'running'
  })
  const completedJobs = jobs.filter((job) => jobStates[job.job_id]?.status === 'done')

  function appendLog(message: string) {
    const stamp = formatIndianTime()
    setLogs((previous) => [...previous, `[${stamp}] ${message}`])
  }

  function addFiles(incoming: File[]) {
    setFiles((previous) => {
      const seen = new Set(previous.map(fileIdentity))
      return [...previous, ...incoming.filter((file) => !seen.has(fileIdentity(file)))]
    })
  }

  async function handleConvert() {
    if (files.length === 0) return
    setConverting(true)
    setError(null)
    setJobs([])
    setJobStates({})
    setLogs([])
    lastEventRef.current = {}
    try {
      const formData = new FormData()
      files.forEach((file) => formData.append('files', file))
      const res = await postForm<{ jobs: ConvertJobEntry[] }>('/tools/erp-to-excel/convert', formData)
      setJobs(res.jobs)
      res.jobs.forEach((job) => appendLog(`Queued ${job.filename}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start conversion.')
    } finally {
      setConverting(false)
    }
  }

  function handleStatusChange(job: ConvertJobEntry, state: JobState<string | null>) {
    setJobStates((previous) => ({ ...previous, [job.job_id]: state }))
    const eventKey = `${state.status}|${state.phase || ''}|${state.error || ''}`
    if (lastEventRef.current[job.job_id] !== eventKey) {
      lastEventRef.current[job.job_id] = eventKey
      appendLog(`${job.filename}: ${state.phase || state.status}${state.error ? ` — ${state.error}` : ''}`)
    }
  }

  function handleDownload(jobId: string, suggestedName: string) {
    const a = document.createElement('a')
    a.href = apiUrl(`/tools/erp-to-excel/download/${jobId}`)
    a.download = suggestedName
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  async function handleDownloadAll() {
    if (completedJobs.length === 0) return
    setSavingAll(true)
    setError(null)
    try {
      const blob = await postBlob('/tools/erp-to-excel/download-all', { job_ids: completedJobs.map((job) => job.job_id) })
      const href = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = href
      a.download = 'ERP_Excel_Conversions.zip'
      a.click()
      URL.revokeObjectURL(href)
      appendLog(`Downloaded ${formatIndianNumber(completedJobs.length)} converted workbook(s) as one ZIP file`)
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Could not save converted files.')
    } finally {
      setSavingAll(false)
    }
  }

  async function handleCancelAll() {
    const active = jobs.filter((job) => {
      const status = jobStates[job.job_id]?.status
      return !status || status === 'queued' || status === 'running'
    })
    await Promise.all(active.map((job) => post(`/tools/erp-to-excel/jobs/${job.job_id}/cancel`)))
    appendLog(`Cancellation requested for ${formatIndianNumber(active.length)} job(s)`)
  }

  function resetAll() {
    if (anyActive) return
    setFiles([])
    setJobs([])
    setJobStates({})
    setLogs([])
    setError(null)
    lastEventRef.current = {}
  }

  return (
    <AppShell title="ERP to Excel Converter">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl"><FileSpreadsheet className="h-5 w-5" /></span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Data transformation</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Convert ERP exports</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">Add files or a complete folder, convert them together, and save every resulting workbook.</p>
            </div>
          </div>

          <FileDropzone
            multiple
            accept=".xls,.xlsx,.htm,.html"
            label="Drag & drop ERP export files here, or click to browse"
            hint="Supports .xls, .xlsx, .htm, .html — multiple files at once"
            files={files}
            onFilesSelected={addFiles}
            onRemove={(index) => setFiles((previous) => previous.filter((_, current) => current !== index))}
          />

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button variant="secondary" icon={<FolderPlus className="h-4 w-4" />} onClick={() => folderInputRef.current?.click()}>
              Add folder
            </Button>
            <Button variant="secondary" icon={<Trash2 className="h-4 w-4" />} disabled={files.length === 0 || anyActive} onClick={() => setFiles([])}>
              Clear files
            </Button>
            <input
              ref={folderInputRef}
              type="file"
              multiple
              accept=".xls,.xlsx,.htm,.html"
              {...({ webkitdirectory: '', directory: '' } as InputHTMLAttributes<HTMLInputElement>)}
              className="hidden"
              onChange={(event) => {
                addFiles(Array.from(event.target.files ?? []))
                event.target.value = ''
              }}
            />
          </div>

          {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>}

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
            <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} disabled={anyActive || (files.length === 0 && jobs.length === 0)} onClick={resetAll}>Reset</Button>
            <Button variant="danger" icon={<Ban className="h-4 w-4" />} disabled={!anyActive} onClick={() => void handleCancelAll()}>Cancel</Button>
            <Button onClick={() => void handleConvert()} loading={converting} disabled={files.length === 0 || anyActive}>Convert all{files.length > 0 ? ` (${formatIndianNumber(files.length)})` : ''}</Button>
          </div>
        </GlassCard>

        {jobs.length > 0 && (
          <GlassCard padding="lg" className="flex flex-col gap-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="font-display text-lg font-semibold text-ink">Conversion progress</h3>
              <Button icon={<Download className="h-4 w-4" />} loading={savingAll} disabled={completedJobs.length === 0} onClick={() => void handleDownloadAll()}>
                Download all
              </Button>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ['Total', stats.total, 'text-ink'],
                ['Converting', stats.running, 'text-accent'],
                ['Completed', stats.completed, 'text-emerald-500'],
                ['Failed', stats.failed, 'text-red-500'],
                ['Cancelled', stats.cancelled, 'text-amber-500'],
              ].map(([label, value, color]) => (
                <div key={String(label)} className="subpanel p-3 text-center"><p className={`text-xl font-bold ${color}`}>{formatIndianNumber(Number(value))}</p><p className="text-xs text-ink-faint">{label}</p></div>
              ))}
            </div>

            <div className="flex flex-col gap-4">
              {jobPagination.pagedItems.map((job) => {
                const state = jobStates[job.job_id]
                return (
                  <div key={job.job_id} className="subpanel flex flex-col gap-3 p-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{job.filename}</span>
                      <div className="flex items-center gap-2">
                        {state?.status === 'done' && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                        {state?.status === 'error' && <XCircle className="h-4 w-4 text-red-500" />}
                        {state?.status === 'done' && (
                          <Button variant="secondary" icon={<Download className="h-4 w-4" />} onClick={() => handleDownload(job.job_id, outputName(job.filename))}>Download</Button>
                        )}
                      </div>
                    </div>
                    <ProgressPanel
                      jobId={job.job_id}
                      poller={pollConvertJob}
                      onStatusChange={(next) => handleStatusChange(job, next)}
                      onError={(message) => setError(message)}
                    />
                  </div>
                )
              })}
            </div>
            <Pagination page={jobPagination.page} pageCount={jobPagination.pageCount} pageSize={jobPagination.pageSize} totalItems={jobPagination.totalItems} pageSizeOptions={[5, 10, 25]} itemLabel="conversion jobs" onPageChange={jobPagination.setPage} onPageSizeChange={jobPagination.setPageSize} />
          </GlassCard>
        )}

        {(logs.length > 0 || jobs.length > 0) && (
          <GlassCard padding="lg" className="flex flex-col gap-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="flex items-center gap-2 font-display text-lg font-semibold text-ink"><Terminal className="h-5 w-5 text-accent" />Activity log</h3>
              <Button variant="ghost" icon={<ListRestart className="h-4 w-4" />} disabled={logs.length === 0} onClick={() => setLogs([])}>Clear log</Button>
            </div>
            <pre className="max-h-72 overflow-auto rounded-xl border border-slate-700 bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-300">{logs.length ? logs.join('\n') : 'No activity yet.'}</pre>
          </GlassCard>
        )}
      </div>
    </AppShell>
  )
}
