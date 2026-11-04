import { useState } from 'react'
import { ReceiptText } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { AutoMailCard } from '@/components/AutoMailCard'
import { SendNowCard } from '@/components/SendNowCard'
import { ReportMailPanel } from '@/components/ReportMailPanel'

const BASE = '/tools/rdc-sales-register-report'
const REPORT_NAME = 'RDC Sales Register Report'

/**
 * The Accounts Sales Register is fetched from the ERP exactly as it is,
 * written to one Excel file per month and mailed from the system email: by
 * hand (with a preview) or automatically on a schedule. Anyone granted the
 * application sets the schedule, frequency and recipients.
 */
export default function RdcSalesRegisterReport() {
  // bumped when a send-now run finishes, so the settings card reloads its last run and send log
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <AppShell title="RDC Sales Register Report">
      <div className="flex flex-col gap-6">
        <GlassCard padding="lg" className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
              <ReceiptText className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-accent">Accounts · Sales</p>
              <h2 className="mt-1.5 font-display text-xl font-semibold tracking-[-0.025em] text-ink">
                RDC Sales Register Report
              </h2>
              <p className="mt-1 text-sm leading-6 text-ink-dim">
                The Accounts Sales Register straight from the ERP, laid out as the ERP’s RDC Sales Invoice
                Details report (same columns, headings and order), with every value exactly as the ERP holds it,
                in Excel. It covers the first of the month through yesterday. Through the first eight days of a month
                the whole previous month is included as well, each month as a file of its own (never two months
                in one file). If a file is too large for an email it is written more compactly and, if needed,
                as .xlsb; a month is never split into several files.
              </p>
            </div>
          </div>
        </GlassCard>

        <ReportMailPanel
          baseUrl={BASE}
          reportName={REPORT_NAME}
          title="Mail this report now"
          description="Fetches the register from the ERP and shows you the exact mail before anything is sent. It always goes from the system email."
        />

        <SendNowCard
          baseUrl={BASE}
          description="Runs the scheduled mail right away: it fetches the register and sends it to the saved recipients, from the system email, with the saved subject and text. It is recorded in the send log below like any scheduled mail. To review the mail first, use Mail this report now above."
          onFinished={() => setRefreshKey((key) => key + 1)}
        />

        <AutoMailCard key={refreshKey} baseUrl={BASE} reportName={REPORT_NAME} showUnmapped={false} />
      </div>
    </AppShell>
  )
}
