import { AsOnCard } from '@/components/AsOnCard'
import { DebtorsMailerPage } from '@/pages/tools/DebtorsReportsAutoMailer'

const BASE = '/tools/meeting-debtors-reports-auto-mailer'

export default function MeetingDebtorsReportsAutoMailer() {
  return (
    <DebtorsMailerPage
      base={BASE}
      title="Meeting Debtors Reports Auto Mailer"
      reportName="Meeting Debtors Reports"
      summary="The Debtors Reports Auto Mailer with the date the reports are as on chosen by you: a calendar date, or the last day of last month. One mail with all three debtors reports, every one of them (and every figure in the mail) as it stood on that date: the receivable ageing with the GST invoice numbers added, the Unapplied Receipts report (its summary in the mail) and the Untagged Invoices report (its summaries in the mail), each as an attachment. Each customer’s Location comes from its ERP region, with the mapping tables below as the fallback (the same tables as the Debtors Reports Auto Mailer)."
      asOnPanel={<AsOnCard baseUrl={BASE} />}
    />
  )
}
