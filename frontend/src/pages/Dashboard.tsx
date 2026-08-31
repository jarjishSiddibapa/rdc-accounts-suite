import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  FileSpreadsheet,
  Receipt,
  ListChecks,
  Scale,
  Combine,
  Banknote,
  BellRing,
  ArrowRight,
  ShieldCheck,
  LayoutGrid,
  PackageCheck,
  WalletCards,
  FilePlus2,
  ChartNoAxesCombined,
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Reveal, RevealGroup } from '@/components/Reveal'
import { useAuth } from '@/lib/auth-context'
import { formatIndianNumber, getIndianHour } from '@/lib/regional'
import { getUserGreetingName } from '@/lib/user'
import { cn } from '@/utils/cn'

type Company = 'RDC' | 'Ultrafine'

const tools = [
  {
    to: '/tools/erp-converter',
    title: 'ERP to Excel Converter',
    description: 'Convert raw ERP exports into clean, formatted Excel workbooks.',
    icon: FileSpreadsheet,
    appKey: 'erp-to-excel',
    category: 'Data preparation',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/rdc-payables',
    title: 'Loans & Advance, IOCL, TDS Report Generator',
    description: 'Generate the loans, advances, IOCL, TDS, and other report with centralized mappings.',
    icon: Receipt,
    appKey: 'rdc-payables',
    category: 'Loans and advances reporting',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/unaccounted-transactions',
    title: 'Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator',
    description: 'Generate all three exception reports, manage their mappings, and send them by email.',
    icon: ListChecks,
    appKey: 'unaccounted',
    category: 'Exception reporting',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/trial-balance',
    title: 'Trial Balance Location Wise Report Generator',
    description: 'Generate the location-wise trial balance report with account mapping.',
    icon: Scale,
    appKey: 'trial-balance',
    category: 'Financial reporting',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/gstr2b-combinator',
    title: 'GSTR 2B File Combinator',
    description: 'Combine multiple GSTR-2B files into one, with editable state-code mappings.',
    icon: Combine,
    appKey: 'gstr2b-combinator',
    category: 'Tax reporting',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/unapplied-receipts',
    title: 'Unapplied Receipts Report Generator',
    description: 'Generate the unapplied receipts report with live Oracle ERP location lookups.',
    icon: Banknote,
    appKey: 'unapplied-receipts',
    category: 'AR reconciliation',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/ultrafine-balance-confirmation',
    title: 'Ultrafine Balance Confirmation Bulk Sender',
    description: 'Send per-customer balance confirmation emails in bulk, with PDF attachments.',
    icon: ShieldCheck,
    appKey: 'ultrafine-balance-confirmation',
    category: 'Ultrafine customer communication',
    company: 'Ultrafine' as Company,
  },
  {
    to: '/tools/ultrafine-payment-reminder',
    title: 'Ultrafine Bulk Payment Reminder Sender',
    description: 'Send per-customer aging/dunning payment reminder emails in bulk.',
    icon: BellRing,
    appKey: 'ultrafine-payment-reminder',
    category: 'Ultrafine customer communication',
    company: 'Ultrafine' as Company,
  },
  {
    to: '/tools/gst-invoice-adder',
    title: 'GST Invoice Number Adder',
    description: 'Enrich GST invoice workbooks with Oracle-backed invoice details.',
    icon: FilePlus2,
    appKey: 'gst-invoice-adder',
    category: 'Tax data enrichment',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/closing-period-report',
    title: 'Closing Period Report Generator',
    description: 'Combine closing-period inventory reports by location into one workbook with a summary.',
    icon: PackageCheck,
    appKey: 'closing-period-report',
    category: 'Inventory reporting',
    company: 'RDC' as Company,
  },
  {
    to: '/tools/iocl-balance',
    title: 'Ultrafine IOCL Balance Monitor',
    description: 'Track the live IOCL CCMS balance and automatically send morning and threshold alerts.',
    icon: WalletCards,
    appKey: 'iocl-balance-monitor',
    category: 'Ultrafine treasury automation',
    company: 'Ultrafine' as Company,
  },
  {
    to: '/tools/creditors-ageing',
    title: 'Ultrafine Creditors Ageing Report Generator',
    description: 'Build classified creditors, advances and intercompany ageing schedules from a fresh Tally export.',
    icon: ChartNoAxesCombined,
    appKey: 'creditors-ageing-report',
    category: 'Ultrafine payables reporting',
    company: 'Ultrafine' as Company,
  },
  {
    to: '/tools/trial-balance-formatter',
    title: 'Ultrafine Trial Balance Formatter',
    description: 'Convert a raw Tally trial balance into the approved Ultrafine workbook format.',
    icon: FileSpreadsheet,
    appKey: 'trial-balance-formatter',
    category: 'Ultrafine financial reporting',
    company: 'Ultrafine' as Company,
  },
]

const COMPANY_FILTERS: Array<{ value: 'All' | Company; label: string }> = [
  { value: 'All', label: 'All Applications' },
  { value: 'RDC', label: 'RDC Applications' },
  { value: 'Ultrafine', label: 'Ultrafine Applications' },
]

export default function Dashboard() {
  const { user } = useAuth()
  const [companyFilter, setCompanyFilter] = useState<'All' | Company>('All')

  const accessibleTools = useMemo(
    () =>
      tools.filter(
        (tool) => user?.allowed_apps == null || user.allowed_apps.includes(tool.appKey),
      ),
    [user],
  )
  const visibleTools = useMemo(
    () =>
      companyFilter === 'All'
        ? accessibleTools
        : accessibleTools.filter((tool) => tool.company === companyFilter),
    [accessibleTools, companyFilter],
  )

  const hour = getIndianHour()
  const timeGreeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const greetingName = user ? getUserGreetingName(user) : 'there'

  return (
    <AppShell title="Dashboard">
      <Reveal>
        <section className="workspace-hero px-6 py-7 sm:px-8 sm:py-9">
          <div className="relative z-10 flex flex-col justify-between gap-7 md:flex-row md:items-end">
            <div className="max-w-2xl">
              <p className="text-sm font-semibold text-accent">
                Accounts workspace
              </p>
              <h2 className="mt-3 font-display text-[clamp(1.875rem,5vw,2.625rem)] leading-[1.1] font-semibold tracking-[-0.03em] text-ink text-balance">
                {timeGreeting}, {greetingName}
              </h2>
              <p className="mt-3 max-w-xl text-sm leading-6 text-ink-dim sm:text-base">
                Your reporting, document, and exception workflows are ready when you are.
              </p>
            </div>
            <div className="workspace-summary grid w-full gap-1 p-2 sm:w-auto sm:min-w-64">
              <div className="flex items-center gap-3 rounded-xl bg-surface px-3.5 py-3">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/10 text-accent">
                  <LayoutGrid className="h-4 w-4" strokeWidth={1.9} />
                </span>
                <span data-numeric className="font-display text-2xl font-semibold text-ink">{formatIndianNumber(accessibleTools.length)}</span>
                <span className="text-xs leading-4 text-ink-faint">applications<br />available</span>
              </div>
              <div className="flex items-center gap-3 px-3.5 py-2.5 text-xs text-ink-dim">
                <ShieldCheck className="h-4 w-4 text-accent" strokeWidth={1.9} />
                Access tailored to your role
              </div>
            </div>
          </div>
        </section>
      </Reveal>

      <div className="mt-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <p className="text-sm font-semibold text-accent">Applications</p>
          <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-[-0.02em] text-ink">Choose what you want to do</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {COMPANY_FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              onClick={() => setCompanyFilter(filter.value)}
              className={cn(
                'rounded-full border px-4 py-2 text-sm font-semibold transition',
                companyFilter === filter.value
                  ? 'border-accent bg-accent text-white'
                  : 'border-border bg-surface text-ink-dim hover:border-accent/50 hover:text-ink',
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>

      <RevealGroup key={companyFilter} className="dashboard-tools-grid mt-5">
        {visibleTools.map((tool, index) => (
          <Reveal key={tool.to}>
            <Link to={tool.to} className="group block h-full rounded-[1.25rem] focus-visible:outline-none">
            <article
              className={cn(
                'tool-card h-full',
                index % 4 === 0 && 'tool-card--featured',
                index % 4 === 3 && 'tool-card--tinted',
              )}
            >
              <div className="relative flex w-full flex-col">
                <div className="flex items-start gap-4">
                  <span className="tool-card-icon grid h-12 w-12 place-items-center rounded-xl bg-accent/10 text-accent">
                    <tool.icon className="h-5 w-5" strokeWidth={1.9} />
                  </span>
                </div>
                <div className="mt-5 flex-1">
                  <p className="tool-card-category text-sm font-medium text-accent">{tool.category}</p>
                  <h3 className="tool-card-title mt-2 max-w-xl font-display text-xl font-semibold tracking-[-0.015em] text-ink">{tool.title}</h3>
                  <p className="tool-card-description mt-2 max-w-md text-sm leading-6 text-ink-dim">{tool.description}</p>
                </div>
                <span className="tool-card-action mt-6 inline-flex w-fit items-center gap-2 text-sm font-semibold text-accent">
                  Open application <ArrowRight className="h-4 w-4" />
                </span>
              </div>
            </article>
            </Link>
          </Reveal>
        ))}
      </RevealGroup>
      {visibleTools.length === 0 && accessibleTools.length > 0 && (
        <div className="glass mt-8 rounded-2xl p-8 text-center">
          <h2 className="font-display text-lg font-semibold text-ink">No applications in this filter</h2>
          <p className="mt-1 text-sm text-ink-dim">Try "All Applications", or pick a different company filter above.</p>
        </div>
      )}
      {accessibleTools.length === 0 && (
        <div className="glass mt-8 rounded-2xl p-8 text-center">
          <h2 className="font-display text-lg font-semibold text-ink">No applications assigned</h2>
          <p className="mt-1 text-sm text-ink-dim">Ask an administrator to grant access to an application.</p>
        </div>
      )}
    </AppShell>
  )
}
