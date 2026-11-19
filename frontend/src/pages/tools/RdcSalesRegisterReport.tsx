import { ReceiptText } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { GlassCard } from '@/components/GlassCard'
import { MailSchedules } from '@/components/MailSchedules'

const BASE = '/tools/rdc-sales-register-report'
const REPORT_NAME = 'the RDC Sales Register Report'

/**
 * The Accounts Sales Register is fetched from the ERP exactly as it is,
 * written to one Excel file per month and mailed from the system email: by
 * hand (preview first, then send) or automatically. Any number of mail schedules, each with its own
 * timing, recipients and wording; anyone granted the application sets them.
 */
export default function RdcSalesRegisterReport() {
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

        <MailSchedules base={BASE} reportName={REPORT_NAME} />
      </div>
    </AppShell>
  )
}
