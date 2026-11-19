import { UserRoundSearch } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { MailSchedules } from '@/components/MailSchedules'

const BASE = '/tools/unidentified-entries'
const REPORT_NAME = 'the Unidentified Entries'

/**
 * The "***** Unidentified" receipts of the Unapplied Receipts Register, fetched
 * from the ERP into one Excel file (the same list as the Unidentified Customers
 * sheet of the Unapplied Receipts report) and mailed from the system email: by
 * hand (preview first, then send) or automatically. Any number of mail schedules, each with its own
 * timing, as-on date, recipients and wording; anyone granted the application sets them.
 */
export default function UnidentifiedEntries() {
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

        <MailSchedules base={BASE} reportName={REPORT_NAME} />
      </div>
    </AppShell>
  )
}
