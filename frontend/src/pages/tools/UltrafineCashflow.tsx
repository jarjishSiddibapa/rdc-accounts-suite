import { useCallback, useEffect, useState } from 'react'
import { Landmark } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { ErrorBanner } from '@/components/ErrorBanner'
import { GlassCard } from '@/components/GlassCard'
import { MailSchedules } from '@/components/MailSchedules'
import { ApiError, get } from '@/lib/api'
import { MappingsPanel } from './cashflow/MappingsPanel'
import { SetupPanel } from './cashflow/SetupPanel'
import { TransactionsPanel } from './cashflow/TransactionsPanel'
import { UploadPanel } from './cashflow/UploadPanel'
import { BASE, currentMonth, useMappingOptions, type MonthRow } from './cashflow/shared'
import { segmentClass } from '@/utils/segment'

type Tab = 'upload' | 'review' | 'setup' | 'mappings' | 'mail'

const TABS: { key: Tab; label: string }[] = [
  { key: 'upload', label: 'Upload and months' },
  { key: 'review', label: 'Review lines' },
  { key: 'setup', label: 'Month setup' },
  { key: 'mappings', label: 'Mappings' },
  { key: 'mail', label: 'Mail schedules' },
]

// What a schedule does when bank lines are not mapped yet (the cashflow is left incomplete without them).
const UNMAPPED_TEXT = {
  label: 'If a bank line is not mapped yet',
  send: 'Send anyway (those lines are highlighted in the workbook and left out of the table)',
  hold: 'Hold the mail until every line is mapped',
}

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
              className={segmentClass(tab === t.key)}
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
        {tab === 'mail' && (
          <>
            <GlassCard padding="lg" className="flex flex-col gap-2">
              <h2 className="font-display text-lg font-semibold text-ink">Mail the cashflow automatically</h2>
              <p className="text-sm leading-6 text-ink-dim">
                Each schedule rebuilds the cashflow of its month from the stored bank lines and the mappings as they
                are at that moment, lays the Summary out as the table in the mail, and attaches the workbook. A
                schedule has its own days and times, recipients, wording, sender and month (this month, last month,
                or a month you pick). It can hold the mail while bank lines are unmapped, so a cashflow with missing
                lines is never mailed by itself. To mail one month once, use Regenerate &amp; mail under Upload and
                months, or Send now on a schedule.
              </p>
            </GlassCard>
            <MailSchedules
              base={BASE}
              reportName="the cashflow"
              showUnmapped
              unmappedText={UNMAPPED_TEXT}
              unmappedHint="Fix them under Mappings or Review lines and preview again, or preview anyway - those lines are highlighted in the workbook and left out of the table."
            />
          </>
        )}
      </div>
    </AppShell>
  )
}
