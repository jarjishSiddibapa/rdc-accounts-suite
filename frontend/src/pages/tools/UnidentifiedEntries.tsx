import { useState } from 'react'
import { UserRoundSearch } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { AutoMailCard } from '@/components/AutoMailCard'
import { SendNowCard } from '@/components/SendNowCard'
import { ReportMailPanel } from '@/components/ReportMailPanel'

const BASE = '/tools/unidentified-entries'
const REPORT_NAME = 'Unidentified Entries'

/**
 * The "***** Unidentified" receipts of the Unapplied Receipts Register, fetched
 * from the ERP into one Excel file (the same list as the Unidentified Customers
 * sheet of the Unapplied Receipts report) and mailed from the system email: by
 * hand (with a preview) or automatically on a schedule. Anyone granted the
 * application sets the schedule, frequency and recipients.
 */
export default function UnidentifiedEntries() {
  // bumped when a send-now run finishes, so the settings card reloads its last run and send log
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <AppShell title="Unidentified Entries">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <UserRoundSearch className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Accounts · Receivables</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                Unidentified Entries
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                The receipts the ERP holds against “***** Unidentified”, straight from the Unapplied Receipts
                Register, each with its Bank Description, in Excel. It is the same list as the Unidentified Customers
                sheet of the Unapplied Receipts report, which keeps its own sheet; here it is mailed on its own.
              </p>
            </div>
          </div>
        </GlassCard>

        <ReportMailPanel
          baseUrl={BASE}
          reportName={REPORT_NAME}
          title="Mail this report now"
          description="Fetches the entries from the ERP and shows you the exact mail before anything is sent. It always goes from the system email."
        />

        <SendNowCard
          baseUrl={BASE}
          description="Runs the scheduled mail right away: it fetches the entries and sends them to the saved recipients, from the system email, with the saved subject and text. It is recorded in the send log below like any scheduled mail. To review the mail first, use Mail this report now above."
          onFinished={() => setRefreshKey((key) => key + 1)}
        />

        <AutoMailCard key={refreshKey} baseUrl={BASE} reportName={REPORT_NAME} showUnmapped={false} />
      </div>
    </AppShell>
  )
}
