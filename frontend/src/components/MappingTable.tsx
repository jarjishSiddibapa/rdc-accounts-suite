import { useMemo, useState } from 'react'
import {
  Archive,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpDown,
  Pencil,
  Plus,
  RotateCcw,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { formatIndianNumber } from '@/lib/regional'
import { Button } from '@/components/Button'
import { Modal } from '@/components/Modal'
import { Pagination } from '@/components/Pagination'
import { SearchBox } from '@/components/SearchBox'
import { usePagination } from '@/hooks/usePagination'
import { CreatableCombobox } from '@/components/CreatableCombobox'

export interface MappingColumn {
  key: string
  label: string
}

export type MappingRow = Record<string, string>

export interface MappingArchiveConfig {
  rows: MappingRow[]
  loading: boolean
  onOpen: () => void | Promise<void>
  onRestore: (index: number) => void | Promise<void>
}

interface MappingTableProps {
  columns: MappingColumn[]
  rows: MappingRow[]
  onAdd: (row: MappingRow) => void | Promise<void>
  onEdit: (index: number, row: MappingRow) => void | Promise<void>
  onDelete: (index: number) => void | Promise<void>
  title?: string
  addLabel?: string
  /** When provided, adds a "View archived" toggle. Soft-deleted rows can
   * be restored, but never permanently deleted. */
  archive?: MappingArchiveConfig
}

/**
 * Generic searchable/sortable CRUD table. Rows are plain string maps keyed
 * by `columns[].key`, so this same component can back any of the mapping
 * tables the tools need (Location -> Incharge, etc.) without bespoke typing
 * per table.
 */
export function MappingTable({
  columns,
  rows,
  onAdd,
  onEdit,
  onDelete,
  title,
  addLabel = 'Add',
  archive,
}: MappingTableProps) {
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [modalMode, setModalMode] = useState<'add' | 'edit' | null>(null)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [form, setForm] = useState<MappingRow>({})
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState<'active' | 'archived'>('active')
  const [restoreBusyIndex, setRestoreBusyIndex] = useState<number | null>(null)

  const sourceRows = view === 'archived' ? (archive?.rows ?? []) : rows

  const filtered = useMemo(() => {
    let result = sourceRows.map((row, index) => ({ row, index }))
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(({ row }) =>
        columns.some((c) => (row[c.key] ?? '').toLowerCase().includes(q)),
      )
    }
    if (sortKey) {
      result = [...result].sort((a, b) => {
        const av = (a.row[sortKey] ?? '').toLowerCase()
        const bv = (b.row[sortKey] ?? '').toLowerCase()
        const cmp = av.localeCompare(bv)
        return sortDir === 'asc' ? cmp : -cmp
      })
    }
    return result
  }, [sourceRows, columns, search, sortKey, sortDir])
  const optionsByColumn = useMemo(
    () =>
      Object.fromEntries(
        columns.map((column) => [
          column.key,
          rows.map((row) => row[column.key] ?? '').filter(Boolean),
        ]),
      ) as Record<string, string[]>,
    [columns, rows],
  )
  const pagination = usePagination(filtered, 10, `${search}\u0000${sortKey ?? ''}\u0000${sortDir}`)

  function toggleSort(key: string) {
    if (sortKey !== key) {
      setSortKey(key)
      setSortDir('asc')
    } else if (sortDir === 'asc') {
      setSortDir('desc')
    } else {
      setSortKey(null)
      setSortDir('asc')
    }
  }

  function openAdd() {
    setForm(Object.fromEntries(columns.map((c) => [c.key, ''])))
    setModalMode('add')
    setEditIndex(null)
  }

  function openEdit(index: number, row: MappingRow) {
    setForm({ ...row })
    setModalMode('edit')
    setEditIndex(index)
  }

  async function handleSubmit() {
    setBusy(true)
    try {
      if (modalMode === 'add') {
        await onAdd(form)
      } else if (modalMode === 'edit' && editIndex !== null) {
        await onEdit(editIndex, form)
      }
      setModalMode(null)
    } finally {
      setBusy(false)
    }
  }

  async function confirmDelete() {
    if (deleteIndex === null) return
    setBusy(true)
    try {
      await onDelete(deleteIndex)
      setDeleteIndex(null)
    } finally {
      setBusy(false)
    }
  }

  async function toggleView() {
    if (!archive) return
    if (view === 'active') {
      setView('archived')
      await archive.onOpen()
    } else {
      setView('active')
    }
  }

  async function handleRestore(index: number) {
    if (!archive) return
    setRestoreBusyIndex(index)
    try {
      await archive.onRestore(index)
    } finally {
      setRestoreBusyIndex(null)
    }
  }

  const isArchivedView = view === 'archived'

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {title && (
          <div className="flex items-center gap-2.5">
            <h3 className="font-display text-lg font-bold tracking-tight text-ink">
              {title}
              {isArchivedView && <span className="ml-2 text-ink-faint">— Archived</span>}
            </h3>
            <span className="rounded-full border border-border bg-bg-soft/70 px-2.5 py-1 text-[10px] font-bold text-ink-faint">
              {formatIndianNumber(sourceRows.length)} rows
            </span>
          </div>
        )}
        <div className="ml-auto flex w-full flex-col items-stretch gap-3 sm:w-auto sm:flex-row sm:items-center">
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search..."
            className="w-full sm:w-auto"
          />
          {archive && (
            <Button
              variant="secondary"
              icon={isArchivedView ? <ArrowLeft className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
              loading={isArchivedView && archive.loading}
              onClick={() => void toggleView()}
            >
              {isArchivedView ? 'Back to active' : 'View archived'}
            </Button>
          )}
          {!isArchivedView && (
            <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={openAdd}>
              {addLabel}
            </Button>
          )}
        </div>
      </div>

      <div className="table-shell">
        <table className="w-full min-w-[480px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-soft/45 text-left">
              {columns.map((c) => (
                <th key={c.key} className="px-4 py-3 font-medium text-ink-dim">
                  <button
                    onClick={() => toggleSort(c.key)}
                    className="inline-flex items-center gap-1 hover:text-ink"
                  >
                    {c.label}
                    {sortKey === c.key ? (
                      sortDir === 'asc' ? (
                        <ArrowUp className="h-3.5 w-3.5" />
                      ) : (
                        <ArrowDown className="h-3.5 w-3.5" />
                      )
                    ) : (
                      <ArrowUpDown className="h-3.5 w-3.5 opacity-40" />
                    )}
                  </button>
                </th>
              ))}
              <th className="px-4 py-3 text-right font-medium text-ink-dim">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={columns.length + 1} className="px-4 py-8 text-center text-ink-faint">
                  {isArchivedView ? 'No archived rows.' : 'No rows found.'}
                </td>
              </tr>
            )}
            {pagination.pagedItems.map(({ row, index }) => (
              <tr
                key={index}
                className={cn(
                  'border-b border-border/80 transition-colors last:border-b-0 hover:bg-bg-soft/55',
                )}
              >
                {columns.map((c) => (
                  <td key={c.key} className="px-4 py-3 text-ink">
                    {row[c.key]}
                  </td>
                ))}
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2">
                    {isArchivedView ? (
                      <button
                        onClick={() => void handleRestore(index)}
                        disabled={restoreBusyIndex === index}
                        aria-label="Restore row"
                        className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-emerald-500 disabled:opacity-50 sm:h-8 sm:w-8"
                      >
                        <RotateCcw className="h-4 w-4" />
                      </button>
                    ) : (
                      <>
                        <button
                          onClick={() => openEdit(index, row)}
                          aria-label="Edit row"
                          className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => setDeleteIndex(index)}
                          aria-label="Archive row"
                          className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-red-500 sm:h-8 sm:w-8"
                        >
                          <Archive className="h-4 w-4" />
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={pagination.page}
        pageCount={pagination.pageCount}
        pageSize={pagination.pageSize}
        totalItems={pagination.totalItems}
        itemLabel="mapping rows"
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />

      <Modal
        open={modalMode !== null}
        onClose={() => setModalMode(null)}
        title={modalMode === 'add' ? addLabel : 'Edit row'}
        className="max-w-2xl"
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void handleSubmit()
          }}
        >
          {columns.map((c) => (
            <label key={c.key} className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium text-ink-dim">{c.label}</span>
              <CreatableCombobox
                value={form[c.key] ?? ''}
                options={optionsByColumn[c.key] ?? []}
                onChange={(value) => setForm((current) => ({ ...current, [c.key]: value }))}
              />
            </label>
          ))}
          <div className="mt-2 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" onClick={() => setModalMode(null)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={busy}>
              Save
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={deleteIndex !== null} onClose={() => setDeleteIndex(null)} title="Archive mapping row">
        <p className="mb-6 text-sm text-ink-dim">
          Archive this mapping row? It will be hidden from active mapping data
          {archive ? ' — you can find it later under "View archived" and restore it.' : ', while its history remains preserved.'}
        </p>
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={() => setDeleteIndex(null)}>
            Cancel
          </Button>
          <Button variant="danger" loading={busy} onClick={() => void confirmDelete()}>
            Archive
          </Button>
        </div>
      </Modal>

    </div>
  )
}
