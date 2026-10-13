import { useCallback, useEffect, useState } from 'react'
import { Landmark } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { ErrorBanner } from '@/components/ErrorBanner'
import { ApiError, get } from '@/lib/api'
import { cn } from '@/utils/cn'
import { MappingsPanel } from './cashflow/MappingsPanel'
import { SetupPanel } from './cashflow/SetupPanel'
import { TransactionsPanel } from './cashflow/TransactionsPanel'
import { UploadPanel } from './cashflow/UploadPanel'
import { BASE, currentMonth, useMappingOptions, type MonthRow } from './cashflow/shared'

type Tab = 'upload' | 'review' | 'setup' | 'mappings'

const TABS: { key: Tab; label: string }[] = [
  { key: 'upload', label: 'Upload and months' },
  { key: 'review', label: 'Review lines' },
  { key: 'setup', label: 'Month setup' },
  { key: 'mappings', label: 'Mappings' },
]

export default function UltrafineCashflow() {
  const [tab, setTab] = useState<Tab>('upload')
  const [months, setMonths] = useState<MonthRow[]>([])
  const [monthsLoading, setMonthsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeMonth, setActiveMonth] = useState('')
  const [options, reloadOptions] = useMappingOptions()

  const reloadMonths = useCallback(async () => {
    try {
      const data = await get<MonthRow[]>(`${BASE}/months`)
      setMonths(data)
      setError(null)
      setActiveMonth((previous) => previous || data[0]?.month || currentMonth())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the months.')
    } finally {
      setMonthsLoading(false)
    }
  }, [])

  useEffect(() => {
    void reloadMonths()
  }, [reloadMonths])

  function open(next: Tab, month: string) {
    setActiveMonth(month)
    setTab(next)
  }

  return (
    <AppShell title="Ultrafine Cashflow Generator">
      <div className="flex flex-col gap-6">
        <div className="flex items-center gap-4">
          <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
            <Landmark className="h-5 w-5" />
          </span>
          <div>
            <p className="text-sm font-semibold text-accent">Bank statements → monthly cashflow</p>
            <p className="mt-1 text-sm leading-6 text-ink-dim">
              Statements are stored once, mapped to names and locations, split by sharing ratios, and turned
              into the Summary, Payments, Receipts and Working sheets.
            </p>
          </div>
        </div>

        <div className="segmented-control" role="tablist" aria-label="Cashflow sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                'rounded-xl px-4 py-2 text-sm font-semibold transition duration-200',
                tab === t.key
                  ? 'bg-accent text-white shadow-[0_8px_18px_-12px_color-mix(in_oklab,var(--color-accent)_75%,transparent)] dark:bg-accent-2'
                  : 'border border-transparent text-ink-dim hover:bg-surface/70 hover:text-ink',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {error && <ErrorBanner>{error}</ErrorBanner>}

        {tab === 'upload' && (
          <UploadPanel
            months={months}
            monthsLoading={monthsLoading}
            options={options}
            reloadMonths={reloadMonths}
            reloadOptions={reloadOptions}
            onReview={(month) => open('review', month)}
            onSetup={(month) => open('setup', month)}
          />
        )}
        {tab === 'review' && (
          <TransactionsPanel
            month={activeMonth}
            onMonthChange={setActiveMonth}
            options={options}
            reloadMonths={reloadMonths}
            reloadOptions={reloadOptions}
          />
        )}
        {tab === 'setup' && (
          <SetupPanel
            month={activeMonth}
            onMonthChange={setActiveMonth}
            options={options}
            onChanged={() => {
              void reloadOptions()
              void reloadMonths()
            }}
          />
        )}
        {tab === 'mappings' && (
          <MappingsPanel
            options={options}
            onChanged={() => {
              void reloadOptions()
              void reloadMonths()
            }}
          />
        )}
      </div>
    </AppShell>
  )
}
