import { useRef, useState, type WheelEvent } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  ExternalLink,
  FileDown,
  FileText,
  FolderDown,
  Image as ImageIcon,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { Pagination } from '@/components/Pagination'
import { usePagination } from '@/hooks/usePagination'
import { ApiError, apiUrl, post } from '@/lib/api'
import { formatIndianNumber } from '@/lib/regional'

interface FetchResponse {
  error: string
  error_detail: string
  mime: string
  fname: string
  final_url: string
  steps: string[]
  size: number
  token: string | null
}

const HISTORY_KEY = 'rdc-dms-recent-urls'
const IMAGE_MIMES = new Set([
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/gif',
  'image/webp',
  'image/bmp',
  'image/tiff',
  'image/x-tiff',
])

function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${formatIndianNumber(bytes)} B`
  if (bytes < 1024 * 1024) {
    return `${formatIndianNumber(bytes / 1024, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KB`
  }
  return `${formatIndianNumber(bytes / (1024 * 1024), { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MB`
}

function normalizeUrl(value: string): string {
  const trimmed = value.trim()
  if (!trimmed || /^https?:\/\//i.test(trimmed)) return trimmed
  return `https://${trimmed}`
}

function loadHistory(): string[] {
  try {
    const value = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]')
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

export default function DmsDownloader() {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<FetchResponse | null>(null)
  const [stepsOpen, setStepsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<string[]>(loadHistory)
  const [zoom, setZoom] = useState(1)
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 })
  const [error, setError] = useState<string | null>(null)
  const viewerRef = useRef<HTMLDivElement>(null)
  const stepPagination = usePagination(result?.steps ?? [], 10, result?.token)

  function rememberUrl(value: string) {
    setHistory((previous) => {
      const next = [value, ...previous.filter((item) => item !== value)].slice(0, 20)
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(next))
      return next
    })
  }

  async function handleFetch() {
    const normalized = normalizeUrl(url)
    if (!normalized) return
    setUrl(normalized)
    setLoading(true)
    setError(null)
    setResult(null)
    setZoom(1)
    setNaturalSize({ width: 0, height: 0 })
    try {
      const res = await post<FetchResponse>('/tools/dms/fetch', { url: normalized })
      setResult(res)
      setStepsOpen(false)
      rememberUrl(normalized)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reach the server.')
    } finally {
      setLoading(false)
    }
  }

  function triggerDownload(path: string, suggestedName: string) {
    const a = document.createElement('a')
    a.href = apiUrl(path)
    a.download = suggestedName
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  function handleDownload() {
    if (!result?.token) return
    triggerDownload(`/tools/dms/download/${result.token}`, result.fname || 'document')
  }

  function handleSavePdf() {
    if (!result?.token) return
    const stem = (result.fname || 'document').replace(/\.[^.]+$/, '')
    triggerDownload(`/tools/dms/pdf/${result.token}`, `${stem}.pdf`)
  }

  function handleOpen() {
    if (!result?.token) return
    window.open(apiUrl(`/tools/dms/view/${result.token}`), '_blank', 'noopener,noreferrer')
  }

  function changeZoom(delta: number) {
    setZoom((value) => Math.min(8, Math.max(0.1, Math.round((value + delta) * 100) / 100)))
  }

  function fitImage() {
    const viewer = viewerRef.current
    if (!viewer || !naturalSize.width || !naturalSize.height) return
    const next = Math.min(
      Math.max(0.1, (viewer.clientWidth - 32) / naturalSize.width),
      Math.max(0.1, (viewer.clientHeight - 32) / naturalSize.height),
      1,
    )
    setZoom(Math.round(next * 100) / 100)
  }

  function handleViewerWheel(event: WheelEvent<HTMLDivElement>) {
    if (!IMAGE_MIMES.has(result?.mime?.toLowerCase() || '')) return
    event.preventDefault()
    changeZoom(event.deltaY < 0 ? 0.1 : -0.1)
  }

  function clearHistory() {
    sessionStorage.removeItem(HISTORY_KEY)
    setHistory([])
    setHistoryOpen(false)
  }

  const hasFetchError = Boolean(result?.error)
  const isHtmlFallback = result?.error === 'HTML_ONLY'
  const canDownload = Boolean(result?.token)
  const isImage = IMAGE_MIMES.has(result?.mime?.toLowerCase() || '')
  const isPdf = result?.mime?.toLowerCase() === 'application/pdf'
  const canSavePdf = canDownload && (isImage || isPdf)
  const viewUrl = result?.token ? apiUrl(`/tools/dms/view/${result.token}`) : ''

  return (
    <AppShell title="DMS Downloader">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-6">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <FolderDown className="h-5 w-5" />
            </span>
            <div>
              <p className="text-xs font-bold tracking-[0.1em] text-accent uppercase">Document retrieval</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">Fetch a DMS document</h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                Paste a DMS permalink, inspect the result, then download, open, or save it as PDF.
              </p>
            </div>
          </div>

          <div className="relative flex flex-col gap-3 sm:flex-row">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleFetch()
              }}
              placeholder="https://dms.example.com/document/..."
              className="field-control flex-1 text-sm"
            />
            <Button variant="secondary" icon={<Clock3 className="h-4 w-4" />} onClick={() => setHistoryOpen((value) => !value)}>
              Recent
            </Button>
            <Button onClick={() => void handleFetch()} loading={loading} disabled={!url.trim()}>
              Fetch
            </Button>

            {historyOpen && (
              <div className="absolute top-full right-0 z-20 mt-2 flex max-h-80 w-full flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl sm:w-[34rem]">
                <div className="flex items-center justify-between border-b border-border px-4 py-3">
                  <span className="text-sm font-semibold text-ink">Recent URLs</span>
                  {history.length > 0 && (
                    <button type="button" className="inline-flex items-center gap-1 text-xs font-medium text-ink-faint hover:text-red-500" onClick={clearHistory}>
                      <Trash2 className="h-3.5 w-3.5" /> Clear
                    </button>
                  )}
                </div>
                {history.length === 0 ? (
                  <p className="px-4 py-6 text-center text-sm text-ink-faint">No URLs fetched yet.</p>
                ) : (
                  <div className="overflow-y-auto p-2">
                    {history.map((item) => (
                      <button
                        key={item}
                        type="button"
                        title={item}
                        className="block w-full truncate rounded-xl px-3 py-2 text-left text-sm text-ink-dim hover:bg-bg-soft hover:text-ink"
                        onClick={() => {
                          setUrl(item)
                          setHistoryOpen(false)
                        }}
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {error && <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p>}

          {result && hasFetchError && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="flex flex-col gap-1 text-sm">
                <span className="font-medium text-amber-700 dark:text-amber-400">{result.error}</span>
                {result.error_detail && <span className="whitespace-pre-wrap text-ink-dim">{result.error_detail}</span>}
                {isHtmlFallback && canDownload && (
                  <span className="mt-1 text-xs text-ink-faint">The viewer page was retained and can still be downloaded or opened for inspection.</span>
                )}
              </div>
            </div>
          )}

          {result && canDownload && (
            <div className="subpanel flex flex-col gap-4 p-4">
              <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div><span className="text-ink-faint">Filename</span><p className="truncate text-ink" title={result.fname}>{result.fname || '—'}</p></div>
                <div><span className="text-ink-faint">Type</span><p className="text-ink">{result.mime || '—'}</p></div>
                <div><span className="text-ink-faint">Size</span><p className="text-ink">{formatSize(result.size)}</p></div>
                <div className="min-w-0"><span className="text-ink-faint">Final URL</span><p className="truncate text-ink" title={result.final_url}>{result.final_url || '—'}</p></div>
              </div>

              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
                <Button variant="secondary" icon={<Download className="h-4 w-4" />} onClick={handleDownload}>Download file</Button>
                <Button
                  variant="secondary"
                  icon={<FileDown className="h-4 w-4" />}
                  onClick={handleSavePdf}
                  disabled={!canSavePdf}
                  title={canSavePdf ? 'Save this image or PDF as a PDF file' : 'PDF conversion is available for images and PDFs'}
                >
                  Save as PDF
                </Button>
                <Button icon={<ExternalLink className="h-4 w-4" />} onClick={handleOpen}>Open</Button>
              </div>
            </div>
          )}

          {result && canDownload && !isHtmlFallback && (
            <div className="overflow-hidden rounded-2xl border border-border bg-bg-soft/60">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-surface/75 px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                  {isImage ? <ImageIcon className="h-4 w-4 text-accent" /> : <FileText className="h-4 w-4 text-accent" />}
                  Document preview
                </div>
                {isImage && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button type="button" aria-label="Zoom out" onClick={() => changeZoom(-0.25)} className="grid h-9 w-9 place-items-center rounded-lg border border-border bg-surface text-ink-dim hover:text-ink"><Minus className="h-4 w-4" /></button>
                    <button type="button" aria-label="Zoom in" onClick={() => changeZoom(0.25)} className="grid h-9 w-9 place-items-center rounded-lg border border-border bg-surface text-ink-dim hover:text-ink"><Plus className="h-4 w-4" /></button>
                    <button type="button" onClick={fitImage} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 text-xs font-semibold text-ink-dim hover:text-ink"><Maximize2 className="h-3.5 w-3.5" /> Fit</button>
                    <button type="button" onClick={() => setZoom(1)} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 text-xs font-semibold text-ink-dim hover:text-ink"><RotateCcw className="h-3.5 w-3.5" /> 1:1</button>
                    <span className="w-14 text-center text-xs font-semibold tabular-nums text-ink-dim">{Math.round(zoom * 100)}%</span>
                  </div>
                )}
              </div>

              <div ref={viewerRef} onWheel={handleViewerWheel} className="relative grid h-[28rem] max-h-[70vh] min-h-72 place-items-center overflow-auto p-4">
                {isImage ? (
                  <img
                    src={viewUrl}
                    alt={result.fname || 'Fetched DMS document'}
                    onLoad={(event) => setNaturalSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
                    draggable={false}
                    className="max-w-none origin-center object-contain shadow-2xl transition-[width,height] duration-150"
                    style={{ width: naturalSize.width ? naturalSize.width * zoom : 'auto', height: naturalSize.height ? naturalSize.height * zoom : 'auto' }}
                  />
                ) : isPdf ? (
                  <iframe title={result.fname || 'PDF preview'} src={viewUrl} className="h-full w-full rounded-xl bg-white" />
                ) : (
                  <div className="flex max-w-lg flex-col items-center gap-3 text-center">
                    <FileText className="h-12 w-12 text-accent" />
                    <p className="font-semibold text-ink">{result.fname}</p>
                    <p className="text-sm leading-6 text-ink-dim">This file type is handled by its installed application. Use Open or Download file above.</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {result && result.steps?.length > 0 && (
            <div className="rounded-xl border border-border">
              <button type="button" onClick={() => setStepsOpen((open) => !open)} className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-ink-dim hover:text-ink">
                <span>Fetch log ({formatIndianNumber(result.steps.length)} steps)</span>
                {stepsOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
              {stepsOpen && (
                <div className="flex flex-col gap-3 border-t border-border bg-slate-950 px-4 py-3">
                  <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-300">{stepPagination.pagedItems.join('\n')}</pre>
                  <Pagination
                    page={stepPagination.page}
                    pageCount={stepPagination.pageCount}
                    pageSize={stepPagination.pageSize}
                    totalItems={stepPagination.totalItems}
                    pageSizeOptions={[10, 25, 50]}
                    itemLabel="fetch steps"
                    onPageChange={stepPagination.setPage}
                    onPageSizeChange={stepPagination.setPageSize}
                    className="border-slate-700 bg-slate-900/80"
                  />
                </div>
              )}
            </div>
          )}
        </GlassCard>
      </div>
    </AppShell>
  )
}
