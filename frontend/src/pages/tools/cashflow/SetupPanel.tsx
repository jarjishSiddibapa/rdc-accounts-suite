import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { MonthYearPicker } from '@/components/TemporalPicker'
import { ApiError, get, put } from '@/lib/api'
import { SummarySettings } from './MappingsPanel'
import { BASE, monthLabel, money, type MappingOptions } from './shared'

interface InputRow {
  cf_location: string
  computed_opening: number
  opening_balance: number | null
  statutory_amount: number | null
}

interface SetupResponse {
  inputs: InputRow[]
}

function numberOrNull(value: string): number | null {
  const parsed = Number.parseFloat(value)
  return value.trim() === '' || Number.isNaN(parsed) ? null : parsed
}

export function SetupPanel({
  month,
  onMonthChange,
  options,
  onChanged,
}: {
  month: string
  onMonthChange: (m: string) => void
  options: MappingOptions
  onChanged: () => void
}) {
  const [inputs, setInputs] = useState<InputRow[]>([])
  const [loadVersion, setLoadVersion] = useState(0)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!month) return
    setLoading(true)
    setError(null)
    try {
      const data = await get<SetupResponse>(`${BASE}/months/${month}/setup`)
      setInputs(data.inputs)
      // the boxes are uncontrolled; a new key re-seeds them from the server, but only per load, never per keystroke
      setLoadVersion((v) => v + 1)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the month setup.')
    } finally {
      setLoading(false)
    }
  }, [month])

  useEffect(() => {
    setSaved(false)
    void load()
  }, [load])

  async function save() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await put(`${BASE}/months/${month}/setup`, {
        inputs: inputs.map((row) => ({
          cf_location: row.cf_location,
          opening_balance: row.opening_balance,
          statutory_amount: row.statutory_amount,
        })),
      })
      setSaved(true)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the month setup.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <GlassCard padding="lg" className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">Month setup</h2>
          <p className="text-sm text-ink-dim">
            Opening balances and statutory amounts per CF location. Regenerate the month afterwards to
            apply changes.
          </p>
        </div>
        <label className="flex flex-col gap-1.5 text-sm sm:w-56">
          <span className="font-medium text-ink-dim">Month</span>
          <MonthYearPicker value={month} onValueChange={onMonthChange} aria-label="Month" />
        </label>
      </div>

      {error && <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>}
      {loading ? (
        <LoadingNotice />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <h3 className="font-display text-base font-semibold text-ink">
              Opening balances — {monthLabel(month)} (₹ lakhs)
            </h3>
            <p className="text-xs text-ink-faint">
              Leave the opening balance empty to carry forward the previous month's closing balance. Type a
              value only for the first month, or to correct a balance by hand.
            </p>
            <div className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-left text-sm">
                <thead className="bg-bg-soft/60 text-ink-dim">
                  <tr>
                    <th className="px-4 py-3 font-medium">CF location</th>
                    <th className="px-4 py-3 text-right font-medium">Opening in use</th>
                    <th className="px-4 py-3 font-medium">Opening balance (override)</th>
                    <th className="px-4 py-3 font-medium">Statutory &amp; others (deducted from closing)</th>
                  </tr>
                </thead>
                <tbody>
                  {inputs.map((row, index) => (
                    <tr key={row.cf_location} className="border-t border-border/80">
                      <td className="px-4 py-2 font-medium text-ink">{row.cf_location}</td>
                      <td className="px-4 py-2 text-right text-ink">{money(row.computed_opening)}</td>
                      <td className="px-4 py-2">
                        <input
                          className="field-control w-40 text-right"
                          inputMode="decimal"
                          aria-label={`Opening balance override for ${row.cf_location}`}
                          placeholder="carried forward"
                          defaultValue={row.opening_balance ?? ''}
                          key={`${month}-${row.cf_location}-open-${loadVersion}`}
                          onChange={(e) =>
                            setInputs((previous) =>
                              previous.map((r, i) =>
                                i === index ? { ...r, opening_balance: numberOrNull(e.target.value) } : r,
                              ),
                            )
                          }
                        />
                      </td>
                      <td className="px-4 py-2">
                        <input
                          className="field-control w-40 text-right"
                          inputMode="decimal"
                          aria-label={`Statutory amount for ${row.cf_location}`}
                          placeholder="0"
                          defaultValue={row.statutory_amount ?? ''}
                          key={`${month}-${row.cf_location}-stat-${loadVersion}`}
                          onChange={(e) =>
                            setInputs((previous) =>
                              previous.map((r, i) =>
                                i === index ? { ...r, statutory_amount: numberOrNull(e.target.value) } : r,
                              ),
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
            {saved && (
              <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                <CheckCircle2 className="h-4 w-4" /> Saved
              </span>
            )}
            <Button loading={saving} onClick={() => void save()}>
              Save month setup
            </Button>
          </div>
        </>
      )}

      <section className="flex flex-col gap-3 border-t border-border pt-6">
        <h3 className="font-display text-base font-semibold text-ink">Summary layout</h3>
        <p className="text-xs text-ink-faint">
          The CF locations as the Summary lists them: order, authorized person, payable percentage and
          subtotal group. These apply to every month.
        </p>
        <SummarySettings options={options} onChanged={onChanged} />
      </section>
    </GlassCard>
  )
}
