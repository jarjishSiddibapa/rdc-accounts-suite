import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthGuard, AdminGuard, AppGuard } from '@/components/AuthGuard'
import { AppErrorBoundary } from '@/components/AppErrorBoundary'

const Login = lazy(() => import('@/pages/Login'))
const ResetPassword = lazy(() => import('@/pages/ResetPassword'))
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Settings = lazy(() => import('@/pages/Settings'))
const Users = lazy(() => import('@/pages/admin/Users'))
const EmailAdministration = lazy(() => import('@/pages/admin/EmailAdministration'))
const SystemMaintenance = lazy(() => import('@/pages/admin/SystemMaintenance'))
const AuditLog = lazy(() => import('@/pages/admin/AuditLog'))
const ErpConverter = lazy(() => import('@/pages/tools/ErpConverter'))
const RdcPayables = lazy(() => import('@/pages/tools/RdcPayables'))
const UnaccountedTransactions = lazy(() => import('@/pages/tools/UnaccountedTransactions'))
const TrialBalance = lazy(() => import('@/pages/tools/TrialBalance'))
const Gstr2bCombinator = lazy(() => import('@/pages/tools/Gstr2bCombinator'))
const UnappliedReceipts = lazy(() => import('@/pages/tools/UnappliedReceipts'))
const UltrafineBalanceConfirmation = lazy(() => import('@/pages/tools/UltrafineBalanceConfirmation'))
const UltrafinePaymentReminder = lazy(() => import('@/pages/tools/UltrafinePaymentReminder'))
const GstInvoiceAdder = lazy(() => import('@/pages/tools/GstInvoiceAdder'))
const ClosingPeriodReport = lazy(() => import('@/pages/tools/ClosingPeriodReport'))
const IoclBalanceMonitor = lazy(() => import('@/pages/tools/IoclBalanceMonitor'))
const CreditorsAgeing = lazy(() => import('@/pages/tools/CreditorsAgeing'))

function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [pathname])
  return null
}

function PageLoading() {
  return (
    <main id="main-content" className="grid min-h-[70vh] place-items-center p-5" role="status" aria-live="polite">
      <div className="glass w-full max-w-md rounded-2xl p-6">
        <div className="h-3 w-24 animate-pulse rounded-md bg-accent/16" />
        <div className="mt-5 h-8 w-48 animate-pulse rounded-lg bg-bg-soft" />
        <div className="mt-3 h-3 w-full animate-pulse rounded-md bg-bg-soft" />
        <div className="mt-2 h-3 w-4/5 animate-pulse rounded-md bg-bg-soft" />
        <div className="mt-7 h-12 w-full animate-pulse rounded-xl bg-bg-soft" />
      </div>
      <span className="sr-only">Loading page</span>
    </main>
  )
}

function protectedPage(page: ReactNode) {
  return <AuthGuard>{page}</AuthGuard>
}

export default function App() {
  return (
    <div className="flex min-h-[100dvh] flex-col">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <ScrollToTop />
      <div className="flex-1">
        <AppErrorBoundary>
          <Suspense fallback={<PageLoading />}>
          <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/reset-password" element={<ResetPassword />} />

          <Route
            path="/"
            element={
              protectedPage(<Dashboard />)
            }
          />

          <Route
            path="/settings"
            element={
              protectedPage(<Settings />)
            }
          />

          <Route
            path="/admin/users"
            element={
              protectedPage(
                <AdminGuard>
                  <Users />
                </AdminGuard>
              )
            }
          />

          <Route
            path="/admin/email"
            element={
              protectedPage(
                <AdminGuard>
                  <EmailAdministration />
                </AdminGuard>
              )
            }
          />
          <Route
            path="/admin/system"
            element={
              protectedPage(
                <AdminGuard>
                  <SystemMaintenance />
                </AdminGuard>
              )
            }
          />
          <Route
            path="/admin/audit-log"
            element={
              protectedPage(
                <AdminGuard>
                  <AuditLog />
                </AdminGuard>
              )
            }
          />

          <Route
            path="/tools/erp-converter"
            element={
              protectedPage(<AppGuard appKey="erp-to-excel"><ErpConverter /></AppGuard>)
            }
          />
          <Route
            path="/tools/rdc-payables"
            element={
              protectedPage(<AppGuard appKey="rdc-payables"><RdcPayables /></AppGuard>)
            }
          />
          <Route
            path="/tools/unaccounted-transactions"
            element={
              protectedPage(<AppGuard appKey="unaccounted"><UnaccountedTransactions /></AppGuard>)
            }
          />
          <Route
            path="/tools/trial-balance"
            element={
              protectedPage(<AppGuard appKey="trial-balance"><TrialBalance /></AppGuard>)
            }
          />
          <Route
            path="/tools/gstr2b-combinator"
            element={
              protectedPage(<AppGuard appKey="gstr2b-combinator"><Gstr2bCombinator /></AppGuard>)
            }
          />
          <Route
            path="/tools/unapplied-receipts"
            element={
              protectedPage(<AppGuard appKey="unapplied-receipts"><UnappliedReceipts /></AppGuard>)
            }
          />
          <Route
            path="/tools/ultrafine-balance-confirmation"
            element={
              protectedPage(
                <AppGuard appKey="ultrafine-balance-confirmation">
                  <UltrafineBalanceConfirmation />
                </AppGuard>
              )
            }
          />
          <Route
            path="/tools/ultrafine-payment-reminder"
            element={
              protectedPage(
                <AppGuard appKey="ultrafine-payment-reminder">
                  <UltrafinePaymentReminder />
                </AppGuard>
              )
            }
          />
          <Route
            path="/tools/gst-invoice-adder"
            element={
              protectedPage(<AppGuard appKey="gst-invoice-adder"><GstInvoiceAdder /></AppGuard>)
            }
          />
          <Route
            path="/tools/closing-period-report"
            element={
              protectedPage(<AppGuard appKey="closing-period-report"><ClosingPeriodReport /></AppGuard>)
            }
          />
          <Route
            path="/tools/iocl-balance"
            element={
              protectedPage(<AppGuard appKey="iocl-balance-monitor"><IoclBalanceMonitor /></AppGuard>)
            }
          />
          <Route
            path="/tools/creditors-ageing"
            element={
              protectedPage(<AppGuard appKey="creditors-ageing-report"><CreditorsAgeing /></AppGuard>)
            }
          />

          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </AppErrorBoundary>
      </div>
    </div>
  )
}
