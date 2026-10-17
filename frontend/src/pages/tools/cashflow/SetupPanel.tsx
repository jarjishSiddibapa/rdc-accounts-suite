import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Copy, Plus, Trash2 } from 'lucide-react'
import { GlassCard } from '@/components/GlassCard'
import { Button } from '@/components/Button'
import { ErrorBanner } from '@/components/ErrorBanner'
import { LoadingNotice } from '@/components/LoadingNotice'
import { MonthYearPicker } from '@/components/TemporalPicker'
import { ApiError, get, post, put } from '@/lib/api'
import { SummarySettings } from './MappingsPanel'
import { BASE, monthLabel, money, type MappingOptions } from './shared'

interface InputRow {
  cf_location: string
  computed_opening: number
  opening_balance: number | null
  statutory_amount: number | null
}

interface PlanItem {
  id?: number
  label: string
  amount_expr: string
}

interface PlanSection {
  id?: number
  block_no: number
  title: string
  items: PlanItem[]
}

interface SetupResponse {
  inputs: InputRow[]
  plan: { block_no: number; sections: { id: number; title: string; items: PlanItem[] }[] }[]
}

function previousMonth(month: string): string {
  const [year, mon] = month.split('-').map(Number)
  const date = new Date(year, mon - 2, 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

/**
 * Mirror of the server's tolerant "20+3+1.5" evaluation, used only for the
 * live total shown while typing. A tiny recursive-descent parser: numbers,
 * + - * / and parentheses, nothing else.
 */
function evaluate(expression: string): number | null {
  const text = expression.replace(/^=/, '').replace(/,/g, '').replace(/\s+/g, '')
  if (!text) return null
  let position = 0

  function parseExpression(): number {
    let value = parseTerm()
    while (text[position] === '+' || text[position] === '-') {
      const operator = text[position++]
      const right = parseTerm()
      value = operator === '+' ? value + right : value - right
    }
    return value
  }

  function parseTerm(): number {
    let value = parseFactor()
    while (text[position] === '*' || text[position] === '/') {
      const operator = text[position++]
      const right = parseFactor()
      value = operator === '*' ? value * right : value / right
    }
    return value
  }

  function parseFactor(): number {
    if (text[position] === '-') {
      position++
      return -parseFactor()
    }
    if (text[position] === '+') {
      position++
      return parseFactor()
    }
    if (text[position] === '(') {
      position++
      const value = parseExpression()
      if (text[position++] !== ')') throw new Error('unbalanced')
      return value
    }
    const match = /^\d+(\.\d+)?|^\.\d+/.exec(text.slice(position))
    if (!match) throw new Error('number expected')
    position += match[0].length
    return Number.parseFloat(match[0])
  }

  try {
    const value = parseExpression()
    return position === text.length && Number.isFinite(value) ? value : null
  } catch {
    return null
  }
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
  const [sections, setSections] = useState<PlanSection[]>([])
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
      setSections(
        data.plan.flatMap((block) =>
          block.sections.map((s) => ({ id: s.id, block_no: block.block_no, title: s.title, items: s.items })),
        ),
      )
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
        plan: sections,
      })
      setSaved(true)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the month setup.')
    } finally {
      setSaving(false)
    }
  }

  async function copyPlan() {
    setError(null)
    try {
      await post(`${BASE}/months/${month}/setup/copy-plan-from/${previousMonth(month)}`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not copy the planning block.')
    }
  }

  function updateSection(index: number, patch: Partial<PlanSection>) {
    setSections((previous) => previous.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }

  function updateItem(sectionIndex: number, itemIndex: number, patch: Partial<PlanItem>) {
    setSections((previous) =>
      previous.map((s, i) =>
        i === sectionIndex
          ? { ...s, items: s.items.map((item, j) => (j === itemIndex ? { ...item, ...patch } : item)) }
          : s,
      ),
    )
  }

  const blockTotals = [1, 2].map((block) =>
    sections
      .filter((s) => s.block_no === block)
      .reduce((sum, s) => sum + s.items.reduce((inner, item) => inner + (evaluate(item.amount_expr) ?? 0), 0), 0),
  )

  return (
    <GlassCard padding="lg" className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">Month setup</h2>
          <p className="text-sm text-ink-dim">
            Opening balances and statutory amounts per CF location, and the planning table printed under
            the Summary. Regenerate the month afterwards to apply changes.
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
                    <th className="px-4 py-3 font-medium">Statutory &amp; others</th>
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

          <section className="flex flex-col gap-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <h3 className="font-display text-base font-semibold text-ink">
                Planning table — {monthLabel(month)} (₹ lakhs)
              </h3>
              <div className="flex flex-col gap-2 sm:flex-row">
                {sections.length === 0 && (
                  <Button variant="secondary" icon={<Copy className="h-4 w-4" />} onClick={() => void copyPlan()}>
                    Copy from {monthLabel(previousMonth(month))}
                  </Button>
                )}
                <Button
                  variant="secondary"
                  icon={<Plus className="h-4 w-4" />}
                  onClick={() =>
                    setSections((previous) => [
                      ...previous,
                      { block_no: 1, title: '', items: [{ label: '', amount_expr: '' }] },
                    ])
                  }
                >
                  Add section
                </Button>
              </div>
            </div>
            <p className="text-xs text-ink-faint">
              Amounts can be a number or a simple sum such as 20+3+1.5. Block 2 is printed as a separate table
              below block 1.
            </p>

            {sections.map((section, sectionIndex) => (
              <div key={sectionIndex} className="subpanel flex flex-col gap-3 p-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <input
                    className="field-control flex-1 font-semibold"
                    placeholder="Section heading (optional)"
                    aria-label="Section heading"
                    value={section.title}
                    onChange={(e) => updateSection(sectionIndex, { title: e.target.value })}
                  />
                  <select
                    className="field-control sm:w-32"
                    aria-label="Block"
                    value={section.block_no}
                    onChange={(e) => updateSection(sectionIndex, { block_no: Number(e.target.value) })}
                  >
                    <option value={1}>Block 1</option>
                    <option value={2}>Block 2</option>
                  </select>
                  <button
                    type="button"
                    aria-label="Remove section"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-red-500"
                    onClick={() => setSections((previous) => previous.filter((_, i) => i !== sectionIndex))}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                {section.items.map((item, itemIndex) => (
                  <div key={itemIndex} className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <input
                      className="field-control flex-1"
                      placeholder="Particulars"
                      aria-label="Particulars"
                      value={item.label}
                      onChange={(e) => updateItem(sectionIndex, itemIndex, { label: e.target.value })}
                    />
                    <input
                      className="field-control text-right sm:w-44"
                      placeholder="Amount, e.g. 20+3+1.5"
                      aria-label="Amount in lakhs"
                      value={item.amount_expr}
                      onChange={(e) => updateItem(sectionIndex, itemIndex, { amount_expr: e.target.value })}
                    />
                    <span className="w-20 text-right text-sm text-ink-dim">
                      {evaluate(item.amount_expr) !== null ? money(evaluate(item.amount_expr) ?? 0) : ''}
                    </span>
                    <button
                      type="button"
                      aria-label="Remove item"
                      className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-ink-dim hover:bg-bg-soft hover:text-red-500"
                      onClick={() =>
                        updateSection(sectionIndex, { items: section.items.filter((_, i) => i !== itemIndex) })
                      }
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
                <div>
                  <Button
                    variant="ghost"
                    className="min-h-9 px-3 py-1.5"
                    icon={<Plus className="h-4 w-4" />}
                    onClick={() =>
                      updateSection(sectionIndex, { items: [...section.items, { label: '', amount_expr: '' }] })
                    }
                  >
                    Add item
                  </Button>
                </div>
              </div>
            ))}
            {sections.length > 0 && (
              <p className="text-sm text-ink-dim">
                Block 1 total: <strong className="text-ink">{money(blockTotals[0])}</strong>
                {blockTotals[1] > 0 && (
                  <>
                    {' '}
                    · Block 2 total: <strong className="text-ink">{money(blockTotals[1])}</strong>
                  </>
                )}
              </p>
            )}
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
          The CF locations as the Summary lists them: order, authorized person, payable and reserve
          percentages and subtotal group. These apply to every month.
        </p>
        <SummarySettings options={options} onChanged={onChanged} />
      </section>
    </GlassCard>
  )
}
