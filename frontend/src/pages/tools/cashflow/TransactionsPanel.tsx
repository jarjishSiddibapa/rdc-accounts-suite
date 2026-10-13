import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Pencil, Plus, Trash2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { Modal } from '@/components/Modal'
import { Pagination } from '@/components/Pagination'
import { SearchBox } from '@/components/SearchBox'
import { LoadingNotice } from '@/components/LoadingNotice'
import { CreatableCombobox } from '@/components/CreatableCombobox'
import { MonthYearPicker } from '@/components/TemporalPicker'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { ApiError, get, put } from '@/lib/api'
import { formatIndianDateTime, formatIndianNumber } from '@/lib/regional'
import { BASE, ISSUE_TEXT, money, type MappingOptions } from './shared'

interface Allocation {
  location: string
  cf_location: string
  amount: number
}

interface Txn {
  id: number
  txn_dt: string
  direction: 'D' | 'C'
  description: string
  reference_no: string
  amount: number
  name: string
  issues: string[]
  manual: boolean
  party_override: boolean
  desc_key: string
  allocations: Allocation[]
  splits: Allocation[]
}

interface TxnPage {
  total: number
  page: number
  page_size: number
  items: Txn[]
}

interface SplitDraft {
  location: string
  cf_location: string
  amount: string
}

function OverrideModal({
  txn,
  options,
  onClose,
  onSaved,
}: {
  txn: Txn | null
  options: MappingOptions
  onClose: () => void
  onSaved: () => void
}) {
  const [party, setParty] = useState('')
  const [partyLocation, setPartyLocation] = useState('')
  const [splits, setSplits] = useState<SplitDraft[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!txn) return
    setParty(txn.party_override ? txn.name : '')
    setPartyLocation('')
    setSplits(
      txn.splits.map((s) => ({ location: s.location, cf_location: s.cf_location, amount: String(s.amount) })),
    )
    setError(null)
  }, [txn])

  const total = useMemo(
    () => splits.reduce((sum, s) => sum + (Number.parseFloat(s.amount) || 0), 0),
    [splits],
  )
  if (!txn) return null
  const difference = Math.round((txn.amount - total) * 100) / 100
  const splitInvalid = splits.length > 0 && (difference !== 0 || splits.some((s) => !s.location.trim()))

  async function save(clear = false) {
    if (!txn) return
    setBusy(true)
    setError(null)
    try {
      await put(`${BASE}/transactions/${txn.id}/override`, {
        party: clear ? null : party.trim() || null,
        party_location: clear ? null : partyLocation.trim() || null,
        splits: clear
          ? []
          : splits.map((s) => ({
              location: s.location,
              cf_location: s.cf_location || null,
              amount: Number.parseFloat(s.amount) || 0,
            })),
      })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the change.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open onClose={onClose} title="Adjust this line" className="max-w-3xl">
      <div className="flex flex-col gap-4">
        <div className="subpanel flex flex-col gap-1 p-3 text-sm">
          <span className="text-ink-faint">
            {formatIndianDateTime(`${txn.txn_dt}+05:30`)} · {txn.direction === 'D' ? 'Payment' : 'Receipt'}
          </span>
          <span className="font-medium break-words text-ink">{txn.description}</span>
          <span className="text-ink">₹{money(txn.amount)}</span>
        </div>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Name for this line only</span>
          <CreatableCombobox
            value={party}
            options={options.parties}
            onChange={setParty}
            placeholder="Leave empty to use the description's mapping"
            suggestionLabel="Existing names"
          />
          <span className="text-xs text-ink-faint">
            Use this when one description belongs to different names (for example TDS or a payer who pays
            for several customers).
          </span>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Location (only if the name is new)</span>
          <CreatableCombobox
            value={partyLocation}
            options={options.locations}
            onChange={setPartyLocation}
            placeholder="Location of the new name"
            suggestionLabel="Existing locations"
          />
        </label>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-ink-dim">Type a split over locations</span>
            <Button
              variant="secondary"
              className="min-h-9 px-3 py-1.5"
              icon={<Plus className="h-4 w-4" />}
              onClick={() =>
                setSplits((previous) => [
                  ...previous,
                  {
                    location: '',
                    cf_location: '',
                    amount: previous.length === 0 ? String(txn.amount) : String(Math.max(difference, 0)),
                  },
                ])
              }
            >
              Add line
            </Button>
          </div>
          {splits.length === 0 && (
            <p className="text-xs text-ink-faint">
              No split typed — the name's location (or its sharing ratio) is used. A typed split replaces
              it completely.
            </p>
          )}
          {splits.map((split, index) => (
            <div key={index} className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <CreatableCombobox
                value={split.location}
                options={options.locations}
                placeholder="Location"
                ariaLabel={`Location of split line ${index + 1}`}
                onChange={(location) =>
                  setSplits((previous) => previous.map((s, i) => (i === index ? { ...s, location } : s)))
                }
                className="sm:flex-1"
              />
              <CreatableCombobox
                value={split.cf_location}
                options={options.cf_locations}
                placeholder="CF location (optional)"
                ariaLabel={`CF location of split line ${index + 1}`}
                onChange={(cf_location) =>
                  setSplits((previous) => previous.map((s, i) => (i === index ? { ...s, cf_location } : s)))
                }
                className="sm:flex-1"
              />
              <input
                className="field-control w-full text-right sm:w-40"
                inputMode="decimal"
                aria-label={`Amount of split line ${index + 1}`}
                value={split.amount}
                onChange={(event) =>
                  setSplits((previous) =>
                    previous.map((s, i) => (i === index ? { ...s, amount: event.target.value } : s)),
                  )
                }
              />
              <button
                type="button"
                aria-label={`Remove split line ${index + 1}`}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-red-500"
                onClick={() => setSplits((previous) => previous.filter((_, i) => i !== index))}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          {splits.length > 0 && (
            <p className={difference === 0 ? 'text-sm text-emerald-500' : 'text-sm text-amber-600'}>
              {difference === 0
                ? 'The split adds up to the bank amount.'
                : `₹${money(Math.abs(difference))} ${difference > 0 ? 'still to allocate' : 'too much'}.`}
            </p>
          )}
        </div>

        {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
          <Button
            variant="ghost"
            disabled={!txn.manual}
            loading={busy}
            onClick={() => void save(true)}
          >
            Remove my changes
          </Button>
          <div className="flex flex-col-reverse gap-3 sm:flex-row">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button loading={busy} disabled={splitInvalid} onClick={() => void save()}>
              Save
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

export function TransactionsPanel({
  month,
  onMonthChange,
  options,
  reloadMonths,
  reloadOptions,
}: {
  month: string
  onMonthChange: (month: string) => void
  options: MappingOptions
  reloadMonths: () => Promise<void>
  reloadOptions: () => Promise<void>
}) {
  const [direction, setDirection] = useState('')
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebouncedValue(search, 300)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [data, setData] = useState<TxnPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<Txn | null>(null)

  const load = useCallback(async () => {
    if (!month) return
    setLoading(true)
    setError(null)
    try {
      const query = new URLSearchParams({
        status,
        q: debouncedSearch,
        page: String(page),
        page_size: String(pageSize),
      })
      if (direction) query.set('direction', direction)
      setData(await get<TxnPage>(`${BASE}/months/${month}/transactions?${query}`))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the transactions.')
    } finally {
      setLoading(false)
    }
  }, [month, direction, status, debouncedSearch, page, pageSize])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setPage(1)
  }, [month, direction, status, debouncedSearch])

  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))

  return (
    <GlassCard padding="lg" className="flex flex-col gap-4">
      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Review transactions</h2>
        <p className="text-sm text-ink-dim">
          Every stored bank line with the name, location and CF location it resolves to. Use the pencil to
          fix a single line; to change what a description means for all lines, edit the mapping instead.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Month</span>
          <MonthYearPicker value={month} onValueChange={onMonthChange} aria-label="Month" />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Type</span>
          <select className="field-control" value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">Payments and receipts</option>
            <option value="D">Payments (debits)</option>
            <option value="C">Receipts (credits)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Show</span>
          <select className="field-control" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All lines</option>
            <option value="issues">Needs fixing</option>
            <option value="manual">Changed by hand</option>
          </select>
        </label>
        <div className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium text-ink-dim">Search</span>
          <SearchBox value={search} onChange={setSearch} placeholder="Description or name" />
        </div>
      </div>

      {error && <ErrorBanner>{error}</ErrorBanner>}
      {loading && !data ? (
        <LoadingNotice />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-bg-soft/60 text-ink-dim">
              <tr>
                <th className="px-3 py-3 font-medium">Date</th>
                <th className="px-3 py-3 font-medium">Description</th>
                <th className="px-3 py-3 font-medium">Name</th>
                <th className="px-3 py-3 text-right font-medium">Amount</th>
                <th className="px-3 py-3 font-medium">Allocated to</th>
                <th className="px-3 py-3 font-medium">Status</th>
                <th className="px-3 py-3 text-right font-medium">Edit</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-ink-faint">
                    No lines match.
                  </td>
                </tr>
              )}
              {(data?.items ?? []).map((txn) => (
                <tr key={txn.id} className="border-t border-border/80 align-top">
                  <td className="px-3 py-3 whitespace-nowrap text-ink">
                    {formatIndianDateTime(`${txn.txn_dt}+05:30`)}
                    <div className={txn.direction === 'D' ? 'text-xs text-red-500' : 'text-xs text-emerald-500'}>
                      {txn.direction === 'D' ? 'Payment' : 'Receipt'}
                    </div>
                  </td>
                  <td className="max-w-md px-3 py-3 break-words text-ink" title={txn.description}>
                    {txn.description}
                  </td>
                  <td className="px-3 py-3 text-ink">{txn.name || <span className="text-ink-faint">—</span>}</td>
                  <td className="px-3 py-3 text-right whitespace-nowrap text-ink">{money(txn.amount)}</td>
                  <td className="px-3 py-3 text-ink">
                    {txn.allocations.map((a, index) => (
                      <div key={index} className="whitespace-nowrap">
                        {a.cf_location || <span className="text-ink-faint">unmapped</span>}
                        {txn.allocations.length > 1 && (
                          <span className="text-ink-faint"> · {money(a.amount)}</span>
                        )}
                        {a.location && a.location !== a.cf_location && (
                          <span className="text-xs text-ink-faint"> ({a.location})</span>
                        )}
                      </div>
                    ))}
                  </td>
                  <td className="px-3 py-3">
                    {txn.issues.length > 0 ? (
                      <span className="inline-flex items-start gap-1 text-xs text-amber-600">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        {txn.issues.map((i) => ISSUE_TEXT[i] ?? i).join('; ')}
                      </span>
                    ) : txn.manual ? (
                      <span className="text-xs text-accent">Changed by hand</span>
                    ) : (
                      <span className="text-xs text-ink-faint">Mapped</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right">
                    <button
                      type="button"
                      aria-label="Adjust this line"
                      onClick={() => setEditing(txn)}
                      className="grid h-11 w-11 place-items-center rounded-full text-ink-dim transition hover:bg-bg-soft hover:text-accent sm:h-8 sm:w-8"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination
        page={page}
        pageCount={pageCount}
        pageSize={pageSize}
        totalItems={data?.total ?? 0}
        pageSizeOptions={[10, 25, 50, 100]}
        itemLabel="lines"
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size)
          setPage(1)
        }}
      />
      <p className="text-xs text-ink-faint">
        {formatIndianNumber(data?.total ?? 0)} line{data?.total === 1 ? '' : 's'} match.
      </p>

      <OverrideModal
        txn={editing}
        options={options}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          void load()
          void reloadMonths()
          void reloadOptions()
        }}
      />
    </GlassCard>
  )
}
