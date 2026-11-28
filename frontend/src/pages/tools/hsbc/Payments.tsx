import { memo, useCallback, useLayoutEffect, useRef, type ClipboardEvent } from 'react'
import { Trash2 } from 'lucide-react'
import {
  FIELDS, FIELD_LABELS, blankEntry, isBlank, parsePasted, type Entry, type EntryField,
} from '@/lib/hsbcTradepay'

const COLUMNS = 'sm:grid-cols-[2.5rem_1fr_1.4fr_1fr_2.75rem]'

interface RowProps {
  entry: Entry
  index: number
  removable: boolean
  onChange: (id: number, field: EntryField, value: string) => void
  onRemove: (id: number) => void
  onPaste: (event: ClipboardEvent<HTMLInputElement>, id: number, field: EntryField) => void
  onEnter: () => void
}

// memo: with a few hundred rows, only the row being typed in is drawn again
const Row = memo(function Row({ entry, index, removable, onChange, onRemove, onPaste, onEnter }: RowProps) {
  return (
    <div className={`grid grid-cols-1 items-center gap-2 border-b border-border pb-2 sm:border-0 sm:pb-0 ${COLUMNS}`}>
      <span className="text-xs text-ink-faint">{index + 1}</span>
      {FIELDS.map((field) => (
        <input key={field} className="field-control w-full" value={entry[field]} placeholder={FIELD_LABELS[field]}
          aria-label={`${FIELD_LABELS[field]}, row ${index + 1}`} autoComplete="off"
          data-entry={field === 'vendor_code' ? entry.id : undefined}
          inputMode={field === 'amount' ? 'decimal' : 'text'}
          onPaste={(e) => onPaste(e, entry.id, field)}
          onKeyDown={(e) => { if (e.key === 'Enter') onEnter() }}
          onChange={(e) => onChange(entry.id, field, e.target.value)} />
      ))}
      {/* out of the Tab order, so Tab goes from the amount straight to the next vendor code */}
      <button type="button" tabIndex={-1} aria-label={`Remove row ${index + 1}`} disabled={!removable}
        className="grid h-11 w-11 place-items-center justify-self-end rounded-lg text-ink-faint transition hover:bg-red-500/10 hover:text-red-500 disabled:invisible"
        onClick={() => onRemove(entry.id)}>
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  )
})

/** The typed rows. There is always an empty one at the end to type or paste into. */
export function PaymentRows({ entries, onChange, onEnter }: {
  entries: Entry[]; onChange: (next: Entry[]) => void; onEnter: () => void
}) {
  const latest = useRef({ entries, onChange, onEnter })
  useLayoutEffect(() => { latest.current = { entries, onChange, onEnter } })

  const change = useCallback((id: number, field: EntryField, value: string) => {
    latest.current.onChange(latest.current.entries.map((x) => (x.id === id ? { ...x, [field]: value } : x)))
  }, [])
  const remove = useCallback((id: number) => {
    latest.current.onChange(latest.current.entries.filter((x) => x.id !== id))
  }, [])
  const enter = useCallback(() => latest.current.onEnter(), [])
  /** Cells copied from Excel fill rows and columns from the box they are pasted into. */
  const paste = useCallback((event: ClipboardEvent<HTMLInputElement>, id: number, field: EntryField) => {
    const text = event.clipboardData.getData('text')
    if (!text.includes('\t') && !text.trim().includes('\n')) return
    event.preventDefault()
    const next = [...latest.current.entries]
    const at = next.findIndex((x) => x.id === id)
    const column = FIELDS.indexOf(field)
    parsePasted(text).forEach((line, offset) => {
      const target = { ...(next[at + offset] ?? blankEntry()) }
      line.slice(0, FIELDS.length - column).forEach((cell, i) => { target[FIELDS[column + i]] = cell })
      next[at + offset] = target
    })
    latest.current.onChange(next)
  }, [])

  return (
    <div className="flex flex-col gap-2">
      <div className={`hidden gap-2 px-1 text-xs font-semibold text-ink-dim sm:grid ${COLUMNS}`}>
        <span>#</span>{FIELDS.map((f) => <span key={f}>{FIELD_LABELS[f]}</span>)}<span />
      </div>
      <div className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto p-1">
        {entries.map((entry, index) => (
          <Row key={entry.id} entry={entry} index={index} removable={entries.length > 1 || !isBlank(entry)}
            onChange={change} onRemove={remove} onPaste={paste} onEnter={enter} />
        ))}
      </div>
    </div>
  )
}
