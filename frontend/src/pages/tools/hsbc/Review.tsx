import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, Info, X, XCircle } from 'lucide-react'
import { Button } from '@/components/Button'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Pagination } from '@/components/Pagination'
import {
  DESCRIPTION_LIMIT, PARTY_LIMIT, amountValue, chosenAccount, cleanAccount, cleanCity, cleanIfsc, cleanParty,
  repeatedKeys, rowProblems, rowWarnings, type ReviewRow, type TextField,
} from '@/lib/hsbcTradepay'
import { formatIndianCurrency, formatIndianDate } from '@/lib/regional'
import { cn } from '@/utils/cn'
import { Field, Step } from './parts'

// ── one checked payment ────────────────────────────────────────────────────────────────────────────────────────────

function PaymentCard({ row, warnings, descriptions, onChange, onRemove }: {
  row: ReviewRow; warnings: string[]; descriptions: string[]
  onChange: (change: Partial<ReviewRow>) => void; onRemove: () => void
}) {
  const problems = rowProblems(row)
  const account = chosenAccount(row)
  const blocked = row.errors.length > 0
  const text = (field: TextField, value: string) =>
    onChange({ [field]: value, edited: row.edited.includes(field) ? row.edited : [...row.edited, field] })
  return (
    <li className={cn('flex flex-col gap-3 rounded-2xl border p-4',
      blocked ? 'border-red-500/40 bg-red-500/5' : problems.length ? 'border-amber-500/50 bg-surface/50' : 'border-border bg-surface/50')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">
            {row.line}. {row.vendor_name || `Vendor ${row.vendor_code || '(blank)'}`}
          </p>
          <p className="text-xs text-ink-dim [overflow-wrap:anywhere]">
            Vendor {row.vendor_code || '—'} · Invoice {row.invoice_no || '—'}
            {row.invoice && (
              <> · ERP: {row.invoice.status}
                {row.invoice.date && <>, dated {formatIndianDate(row.invoice.date)}</>}
                , amount {formatIndianCurrency(Number(row.invoice.amount))}
                , unpaid {formatIndianCurrency(Number(row.invoice.open))}
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {problems.length === 0 && warnings.length === 0 && <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-label="Ready" />}
          <span className="text-sm font-semibold text-ink">{blocked ? row.amount : formatIndianCurrency(amountValue(row.amount))}</span>
          <button type="button" className="grid h-9 w-9 place-items-center rounded-lg text-ink-faint transition hover:bg-red-500/10 hover:text-red-500"
            aria-label={`Leave payment ${row.line} out of the file`} title="Leave this payment out" onClick={onRemove}>
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {!blocked && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1.7fr_0.75fr_0.95fr_1.8fr]">
          <Field label="Party name" note={`${row.party_name.length} of ${PARTY_LIMIT} characters`}>
            <input className="field-control w-full" value={row.party_name} maxLength={PARTY_LIMIT} aria-label={`Party name, row ${row.line}`}
              onChange={(e) => text('party_name', cleanParty(e.target.value))} />
          </Field>
          <Field label="City">
            <input className="field-control w-full" value={row.city} aria-label={`City, row ${row.line}`}
              onChange={(e) => text('city', cleanCity(e.target.value))} />
          </Field>
          <Field label="Description of payment">
            <CreatableCombobox value={row.description} options={descriptions} ariaLabel={`Description, row ${row.line}`}
              suggestionLabel="Descriptions" showAllOnOpen
              onChange={(value) => text('description', value.slice(0, DESCRIPTION_LIMIT))} />
          </Field>
          {row.manual_account ? (
            <Field label="Bank account (typed by you)">
              <div className="grid grid-cols-[1.2fr_1fr] gap-2">
                <input className="field-control w-full" placeholder="Account number" value={row.typed_account} inputMode="text"
                  aria-label={`Account number, row ${row.line}`} autoComplete="off"
                  onChange={(e) => onChange({ typed_account: cleanAccount(e.target.value) })} />
                <input className="field-control w-full" placeholder="IFSC" value={row.typed_ifsc} autoComplete="off"
                  aria-label={`IFSC, row ${row.line}`}
                  onChange={(e) => onChange({ typed_ifsc: cleanIfsc(e.target.value) })} />
              </div>
            </Field>
          ) : (
            <Field label="Bank account (from the ERP)" note={account && (
              <>{[account.bank, account.branch].filter(Boolean).join(', ')}{account.beneficiary && <> · in the name of {account.beneficiary}</>}</>
            )}>
              {row.accounts.length === 1 ? (
                <p className="field-control flex min-h-11 items-center font-mono text-sm [overflow-wrap:anywhere]">{row.accounts[0].account} · {row.accounts[0].ifsc}</p>
              ) : (
                <select className={cn('field-control w-full', !row.account_key && 'border-amber-500')} value={row.account_key}
                  aria-label={`Bank account, row ${row.line}`} onChange={(e) => onChange({ account_key: e.target.value })}>
                  <option value="">Choose one of {row.accounts.length} accounts…</option>
                  {row.accounts.map((a) => (
                    <option key={a.key} value={a.key}>
                      {a.account} · {a.ifsc}{a.bank ? ` · ${a.bank}` : ''}{a.locations.length ? ` · ${a.locations.join('/')}` : ''}{a.problem ? ' · wrongly entered' : ''}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
        </div>
      )}

      {(problems.length > 0 || warnings.length > 0 || row.notes.length > 0) && (
        <ul className="flex flex-col gap-1">
          {problems.map((message) => (
            <li key={message} className="flex items-start gap-2 text-xs font-medium text-red-500">
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{message}
            </li>
          ))}
          {warnings.map((message) => (
            <li key={message} className="flex items-start gap-2 text-xs text-amber-600 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{message}
            </li>
          ))}
          {row.notes.map((message) => (
            <li key={message} className="flex items-start gap-2 text-xs text-ink-faint">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />{message}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

// ── step 3: all of them, and the download ──────────────────────────────────────────────────────────────────────────

type Show = 'all' | 'action' | 'warning'

const PAGE_SIZES = [10, 25, 50]

export function Review({ rows, descriptions, fileName, blockers, acknowledged, making, error, onAcknowledge, onChange,
  onRemove, onDownload }: {
  rows: ReviewRow[]
  descriptions: string[]
  fileName: string
  /** What stops the download, other than the rows themselves. */
  blockers: string[]
  acknowledged: boolean
  making: boolean
  error: string | null
  onAcknowledge: (value: boolean) => void
  onChange: (id: number, change: Partial<ReviewRow>) => void
  onRemove: (id: number) => void
  onDownload: () => void
}) {
  // "need action" and "with warnings" hold the rows that matched when chosen, so a row does not vanish while it is
  // being corrected
  const [view, setView] = useState<{ show: Show; ids: Set<number> | null }>({ show: 'all', ids: null })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)

  const repeated = useMemo(() => repeatedKeys(rows), [rows])
  const unready = rows.filter((r) => rowProblems(r).length > 0)
  const warned = rows.filter((r) => rowWarnings(r, repeated).length > 0)
  const total = rows.reduce((sum, r) => sum + (r.errors.length ? 0 : amountValue(r.amount)), 0)

  const listed = view.ids ? rows.filter((r) => view.ids!.has(r.entry_id)) : rows
  const visible = listed.length ? listed : rows
  const pageCount = Math.max(1, Math.ceil(visible.length / pageSize))
  const current = Math.min(page, pageCount)

  function show(next: Show) {
    const list = next === 'action' ? unready : next === 'warning' ? warned : null
    setView({ show: next, ids: list && new Set(list.map((r) => r.entry_id)) })
    setPage(1)
  }

  const waiting = [...blockers]
  if (unready.length) waiting.push(`${unready.length} payment${unready.length > 1 ? 's need' : ' needs'} your action (marked in red).`)
  if (warned.length && !acknowledged) waiting.push('Tick that you have checked the warnings.')

  const chips: { key: Show; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: rows.length },
    { key: 'action', label: 'Need your action', count: unready.length },
    { key: 'warning', label: 'With warnings', count: warned.length },
  ]
  const shown = listed.length ? view.show : 'all'

  return (
    <Step n={3} title="Check and download" hint="These are the values that go into the file. Correct anything that is not right.">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Which payments to show">
        {chips.filter((c) => c.key === 'all' || c.count > 0).map((c) => (
          <button key={c.key} type="button" aria-pressed={shown === c.key} onClick={() => show(c.key)}
            className={cn('min-h-10 rounded-full border px-4 text-sm font-semibold transition',
              shown === c.key ? 'border-accent bg-accent/10 text-accent' : 'border-border text-ink-dim hover:border-accent/40 hover:text-ink')}>
            {c.label} · {c.count}
          </button>
        ))}
      </div>
      <ul className="flex flex-col gap-3">
        {visible.slice((current - 1) * pageSize, current * pageSize).map((row) => (
          <PaymentCard key={row.entry_id} row={row} warnings={rowWarnings(row, repeated)} descriptions={descriptions}
            onChange={(change) => onChange(row.entry_id, change)} onRemove={() => onRemove(row.entry_id)} />
        ))}
      </ul>
      {visible.length > PAGE_SIZES[0] && (
        <Pagination page={current} pageCount={pageCount} pageSize={pageSize} totalItems={visible.length} itemLabel="payments"
          pageSizeOptions={PAGE_SIZES} onPageChange={setPage} onPageSizeChange={(size) => { setPageSize(size); setPage(1) }} />
      )}
      <div className="flex flex-col gap-3 border-t border-border pt-4">
        <p className="text-sm text-ink">
          <strong>{rows.length}</strong> payment{rows.length > 1 ? 's' : ''}, total <strong>{formatIndianCurrency(total)}</strong>
        </p>
        {warned.length > 0 && (
          <label className="flex cursor-pointer items-start gap-2.5 text-sm text-ink">
            <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={acknowledged}
              onChange={(e) => onAcknowledge(e.target.checked)} />
            I have checked the warnings on {warned.length} payment{warned.length > 1 ? 's' : ''}.
          </label>
        )}
        {waiting.length > 0 && (
          <ul className="list-disc pl-5 text-xs leading-5 text-ink-dim">
            {waiting.map((b) => <li key={b}>{b}</li>)}
          </ul>
        )}
        {error && <ErrorBanner>{error}</ErrorBanner>}
        <div>
          <Button onClick={onDownload} loading={making} disabled={waiting.length > 0} icon={<Download className="h-4 w-4" />}>
            Download {fileName || 'CSV'}
          </Button>
        </div>
      </div>
    </Step>
  )
}
