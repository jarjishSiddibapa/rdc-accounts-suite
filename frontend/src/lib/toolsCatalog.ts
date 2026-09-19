import type { LucideIcon } from 'lucide-react'
import {
  FileSpreadsheet,
  Receipt,
  ListChecks,
  Scale,
  Combine,
  Banknote,
  BellRing,
  ShieldCheck,
  PackageCheck,
  WalletCards,
  FilePlus2,
  ChartNoAxesCombined,
  ClipboardCheck,
  Megaphone,
} from 'lucide-react'

export type Company = 'RDC' | 'Ultrafine'

export interface ToolCatalogEntry {
  to: string
  title: string
  description: string
  icon: LucideIcon
  appKey: string
  category: string
  company: Company
}

/**
 * Single source of truth for every registered tool - the Dashboard's tool
 * grid and the Login page's marketing copy both read from this list, so
 * adding, renaming, or removing a tool here is the only edit needed for
 * both surfaces to stay in sync.
 */
export const TOOLS_CATALOG: ToolCatalogEntry[] = [
  {
    to: '/tools/erp-converter',
    title: 'ERP to Excel Converter',
    description: 'Convert raw ERP exports into clean, formatted Excel workbooks.',
    icon: FileSpreadsheet,
    appKey: 'erp-to-excel',
    category: 'Data preparation',
    company: 'RDC',
  },
  {
    to: '/tools/rdc-payables',
    title: 'Loans & Advance, IOCL, TDS Report Generator',
    description: 'Generate the loans, advances, IOCL, TDS, and other report with centralized mappings.',
    icon: Receipt,
    appKey: 'rdc-payables',
    category: 'Loans and advances reporting',
    company: 'RDC',
  },
  {
    to: '/tools/unaccounted-transactions',
    title: 'Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator',
    description: 'Generate all three exception reports, manage their mappings, and send them by email.',
    icon: ListChecks,
    appKey: 'unaccounted',
    category: 'Exception reporting',
    company: 'RDC',
  },
  {
    to: '/tools/trial-balance',
    title: 'Trial Balance Location Wise Report Generator',
    description: 'Generate the location-wise trial balance report with account mapping.',
    icon: Scale,
    appKey: 'trial-balance',
    category: 'Financial reporting',
    company: 'RDC',
  },
  {
    to: '/tools/gstr2b-combinator',
    title: 'GSTR-2B File Combinator',
    description: 'Combine multiple GSTR-2B files into one, with editable state-code mappings.',
    icon: Combine,
    appKey: 'gstr2b-combinator',
    category: 'Tax reporting',
    company: 'RDC',
  },
  {
    to: '/tools/unapplied-receipts',
    title: 'Unapplied Receipts Report Generator',
    description: 'Generate the unapplied receipts report with live Oracle ERP location lookups.',
    icon: Banknote,
    appKey: 'unapplied-receipts',
    category: 'AR reconciliation',
    company: 'RDC',
  },
  {
    to: '/tools/ultrafine-balance-confirmation',
    title: 'Ultrafine Balance Confirmation Bulk Sender',
    description: 'Send per-customer balance confirmation emails in bulk, with PDF attachments.',
    icon: ShieldCheck,
    appKey: 'ultrafine-balance-confirmation',
    category: 'Ultrafine customer communication',
    company: 'Ultrafine',
  },
  {
    to: '/tools/ultrafine-payment-reminder',
    title: 'Ultrafine Bulk Payment Reminder Sender',
    description: 'Send per-customer aging/dunning payment reminder emails in bulk.',
    icon: BellRing,
    appKey: 'ultrafine-payment-reminder',
    category: 'Ultrafine customer communication',
    company: 'Ultrafine',
  },
  {
    to: '/tools/ultrafine-fse-reminder',
    title: 'Ultrafine FSE Bulk Reminder',
    description: 'Send per-FSE collection vs target reminders, plus one combined broadcast to management.',
    icon: Megaphone,
    appKey: 'ultrafine-fse-reminder',
    category: 'Ultrafine customer communication',
    company: 'Ultrafine',
  },
  {
    to: '/tools/gst-invoice-adder',
    title: 'GST Invoice Number Adder',
    description: 'Enrich GST invoice workbooks with Oracle-backed invoice details.',
    icon: FilePlus2,
    appKey: 'gst-invoice-adder',
    category: 'Tax data enrichment',
    company: 'RDC',
  },
  {
    to: '/tools/closing-period-report',
    title: 'Closing Period Report Generator',
    description: 'Combine closing-period inventory reports by location into one workbook with a summary.',
    icon: PackageCheck,
    appKey: 'closing-period-report',
    category: 'Inventory reporting',
    company: 'RDC',
  },
  {
    to: '/tools/iocl-balance',
    title: 'Ultrafine IOCL Balance Monitor',
    description: 'Track the live IOCL CCMS balance and automatically send morning and threshold alerts.',
    icon: WalletCards,
    appKey: 'iocl-balance-monitor',
    category: 'Ultrafine treasury automation',
    company: 'Ultrafine',
  },
  {
    to: '/tools/invoice-booking-tracker',
    title: 'Ultrafine Invoice Booking Tracker',
    description: 'Check every DMS work-queue page, track pending invoice bookings, and send the daily tracker automatically.',
    icon: ClipboardCheck,
    appKey: 'invoice-booking-tracker',
    category: 'Ultrafine invoice workflow automation',
    company: 'Ultrafine',
  },
  {
    to: '/tools/creditors-ageing',
    title: 'Ultrafine Creditors Ageing Report Generator',
    description: 'Build classified creditors, advances and intercompany ageing schedules from a fresh Tally export.',
    icon: ChartNoAxesCombined,
    appKey: 'creditors-ageing-report',
    category: 'Ultrafine payables reporting',
    company: 'Ultrafine',
  },
  {
    to: '/tools/trial-balance-formatter',
    title: 'Ultrafine Trial Balance Formatter',
    description: 'Convert a raw Tally trial balance into the approved Ultrafine workbook format.',
    icon: FileSpreadsheet,
    appKey: 'trial-balance-formatter',
    category: 'Ultrafine financial reporting',
    company: 'Ultrafine',
  },
]
