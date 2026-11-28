import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Trash2 } from 'lucide-react'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { GlassCard } from '@/components/GlassCard'
import { Modal } from '@/components/Modal'
import { Pagination } from '@/components/Pagination'
import { SearchBox } from '@/components/SearchBox'
import { apiUrl, del, get } from '@/lib/api'
import { BASE, type MadeFile } from '@/lib/hsbcTradepay'
import { formatIndianCurrency, formatIndianDate, formatIndianDateTime } from '@/lib/regional'
import { message } from './parts'

/** The files made so far. */
export function FileHistory({ version, onRemoved }: { version: number; onRemoved: () => void }) {
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [files, setFiles] = useState<MadeFile[]>([])
  const [total, setTotal] = useState(0)
  const [removing, setRemoving] = useState<MadeFile | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(0)

  const load = useCallback(async () => {
    const asked = ++latest.current
    try {
      const params = new URLSearchParams({ q: query, page: String(page), page_size: String(pageSize) })
      const reply = await get<{ total: number; items: MadeFile[] }>(`${BASE}/files?${params}`)
      if (asked !== latest.current) return                      // an older search answering late
      if (!reply.items.length && page > 1) {                   // the last file of a page was removed
        setPage(page - 1)
        return
      }
      setFiles(reply.items)
      setTotal(reply.total)
      setError(null)
    } catch (err) {
      if (asked === latest.current) setError(message(err, 'The list of files could not be loaded.'))
    }
  }, [query, page, pageSize])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), query ? 300 : 0)
    return () => window.clearTimeout(timer)
  }, [load, query, version])

  async function remove() {
    if (!removing) return
    setBusy(true)
    try {
      await del(`${BASE}/files/${removing.id}`)
      setRemoving(null)
      onRemoved()                                              // reloads this list and the next series number
    } catch (err) {
      setError(message(err, 'The file could not be removed.'))
      setRemoving(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="font-display text-lg font-semibold text-ink">Files made</h3>
        <SearchBox value={query} onChange={(v) => { setQuery(v); setPage(1) }} className="sm:w-80"
          placeholder="File code, invoice or vendor code" />
      </div>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {files.length === 0 ? (
        <p className="text-sm text-ink-dim">{query ? 'No file matches this search.' : 'No file has been made yet.'}</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[40rem] text-sm">
            <thead className="bg-bg-soft/70 text-left text-xs text-ink-dim">
              <tr>
                {['File code', 'Date in file', 'Payments', 'Total', 'Made by', ''].map((h) => (
                  <th key={h} className="px-3 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.id} className="border-t border-border">
                  <td className="px-3 py-2 font-semibold text-ink">{f.file_code}</td>
                  <td className="px-3 py-2 text-ink-dim">{formatIndianDate(f.file_date)}</td>
                  <td className="px-3 py-2 text-ink-dim">{f.rows}</td>
                  <td className="px-3 py-2 text-ink-dim">{formatIndianCurrency(Number(f.total))}</td>
                  <td className="px-3 py-2 text-ink-dim">{f.made_by}<span className="block text-xs text-ink-faint">{formatIndianDateTime(f.made_at)}</span></td>
                  <td className="px-3 py-2">
                    {f.mine && (
                      <div className="flex justify-end gap-1">
                        <a className="grid h-9 w-9 place-items-center rounded-lg text-ink-dim transition hover:bg-bg-soft hover:text-accent"
                          href={apiUrl(`${BASE}/files/${f.id}/download`)} aria-label={`Download ${f.file_code}`} title="Download again">
                          <Download className="h-4 w-4" />
                        </a>
                        <button type="button" className="grid h-9 w-9 place-items-center rounded-lg text-ink-dim transition hover:bg-red-500/10 hover:text-red-500"
                          aria-label={`Remove ${f.file_code}`} title="Remove from this list" onClick={() => setRemoving(f)}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination page={page} pageCount={Math.max(1, Math.ceil(total / pageSize))} pageSize={pageSize} totalItems={total}
        onPageChange={setPage} onPageSizeChange={(size) => { setPageSize(size); setPage(1) }} itemLabel="files" />
      <Modal open={removing !== null} onClose={() => setRemoving(null)} title={`Remove ${removing?.file_code ?? ''}?`}>
        <div className="flex flex-col gap-4">
          <p className="text-sm leading-6 text-ink-dim">
            Use this for a file that was not uploaded to the bank. Its file code becomes free again and its invoices are
            no longer reported as already used. It does not cancel anything at the bank.
          </p>
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="secondary" onClick={() => setRemoving(null)}>Keep it</Button>
            <Button variant="danger" loading={busy} onClick={() => void remove()}>Remove</Button>
          </div>
        </div>
      </Modal>
    </GlassCard>
  )
}
