import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { motion, useReducedMotion } from 'motion/react'
import { SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from '@/lib/sidebarLayout'
import {
  LayoutDashboard,
  FileSpreadsheet,
  Receipt,
  ListChecks,
  Scale,
  Combine,
  Banknote,
  ShieldCheck,
  BellRing,
  FileCheck2,
  PackageCheck,
  DatabaseBackup,
  ClipboardList,
  Users,
  Settings as SettingsIcon,
  LogOut,
  Layers,
  Mail,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  WalletCards,
  ChartNoAxesCombined,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { useAuth } from '@/lib/auth-context'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  appKey?: string
}

const mainNav: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/tools/erp-converter', label: 'ERP to Excel', icon: FileSpreadsheet, appKey: 'erp-to-excel' },
  { to: '/tools/rdc-payables', label: 'RDC Payables', icon: Receipt, appKey: 'rdc-payables' },
  {
    to: '/tools/unaccounted-transactions',
    label: 'Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator',
    icon: ListChecks,
    appKey: 'unaccounted',
  },
  { to: '/tools/trial-balance', label: 'Trial Balance Location Wise', icon: Scale, appKey: 'trial-balance' },
  { to: '/tools/gstr2b-combinator', label: 'GSTR 2B File Combinator', icon: Combine, appKey: 'gstr2b-combinator' },
  { to: '/tools/unapplied-receipts', label: 'Unapplied Receipts Report Generator', icon: Banknote, appKey: 'unapplied-receipts' },
  {
    to: '/tools/ultrafine-balance-confirmation',
    label: 'Ultrafine Balance Confirmation',
    icon: ShieldCheck,
    appKey: 'ultrafine-balance-confirmation',
  },
  {
    to: '/tools/ultrafine-payment-reminder',
    label: 'Ultrafine Payment Reminder',
    icon: BellRing,
    appKey: 'ultrafine-payment-reminder',
  },
  { to: '/tools/gst-invoice-adder', label: 'GST Invoice Number Adder', icon: FileCheck2, appKey: 'gst-invoice-adder' },
  { to: '/tools/closing-period-report', label: 'Closing Period Report Generator', icon: PackageCheck, appKey: 'closing-period-report' },
  { to: '/tools/iocl-balance', label: 'Ultrafine IOCL Balance Monitor', icon: WalletCards, appKey: 'iocl-balance-monitor' },
  { to: '/tools/creditors-ageing', label: 'Ultrafine Creditors Ageing Report Generator', icon: ChartNoAxesCombined, appKey: 'creditors-ageing-report' },
  { to: '/tools/trial-balance-formatter', label: 'Ultrafine Trial Balance Formatter', icon: FileSpreadsheet, appKey: 'trial-balance-formatter' },
]

interface SidebarProps {
  className?: string
  mobileOpen?: boolean
  collapsed?: boolean
  onCollapseToggle?: () => void
  width?: number
  onWidthChange?: (width: number) => void
  onNavigate?: () => void
}

/** True only at the desktop (xl) breakpoint, where the sidebar is a
 * persistent column rather than a mobile overlay drawer; resizing only
 * makes sense there. */
function useIsDesktopSidebar() {
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 1280px)').matches,
  )
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1280px)')
    const onChange = () => setIsDesktop(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])
  return isDesktop
}

export function Sidebar({
  className,
  mobileOpen = false,
  collapsed = false,
  onCollapseToggle,
  width,
  onWidthChange,
  onNavigate,
}: SidebarProps) {
  const { user, logout } = useAuth()
  const isDesktop = useIsDesktopSidebar()
  const reduceMotion = useReducedMotion()
  const [resizing, setResizing] = useState(false)
  const dragStart = useRef({ x: 0, width: width ?? SIDEBAR_DEFAULT_WIDTH })

  useEffect(() => {
    if (!resizing) return
    function handleMove(event: PointerEvent) {
      const next = dragStart.current.width + (event.clientX - dragStart.current.x)
      onWidthChange?.(Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(next))))
    }
    function handleUp() {
      setResizing(false)
    }
    window.addEventListener('pointermove', handleMove)
    window.addEventListener('pointerup', handleUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerup', handleUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [resizing, onWidthChange])

  // null allowed_apps = unrestricted (show everything); a list = only show
  // the tools that list includes. Items with no appKey (Dashboard) always show.
  const visibleNav = mainNav.filter(
    (item) => !item.appKey || user?.allowed_apps == null || user.allowed_apps.includes(item.appKey),
  )

  return (
    <motion.aside
      id="primary-navigation"
      aria-label="Primary navigation"
      layout={!resizing}
      transition={{ layout: { type: 'spring', stiffness: 380, damping: 36, mass: 0.8 } }}
      style={isDesktop && !collapsed && width ? { width: `${width}px` } : undefined}
      className={cn(
        'glass relative flex w-64 shrink-0 flex-col gap-5 overflow-hidden rounded-[1.25rem] p-3.5',
        !resizing && 'transition-transform duration-300',
        'xl:translate-x-0',
        collapsed ? 'xl:w-20 xl:px-3' : 'xl:w-64',
        mobileOpen ? 'translate-x-0' : '-translate-x-[calc(100%+1rem)]',
        className,
      )}
    >
      {isDesktop && !collapsed && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          onPointerDown={(event) => {
            dragStart.current = { x: event.clientX, width: width ?? SIDEBAR_DEFAULT_WIDTH }
            setResizing(true)
          }}
          onDoubleClick={() => onWidthChange?.(SIDEBAR_DEFAULT_WIDTH)}
          title="Drag to resize, double-click to reset"
          className="absolute top-0 right-0 z-10 hidden h-full w-2 -translate-x-1/2 cursor-col-resize touch-none xl:block hover:bg-accent/25"
        />
      )}
      <div className={cn('flex min-h-12 items-center gap-2 px-1.5 pt-0.5', collapsed && 'xl:justify-center xl:px-0')}>
        <div className={cn('flex min-w-0 items-center gap-2', collapsed && 'xl:hidden')}>
          <span className="icon-tile grid h-10 w-10 shrink-0 place-items-center rounded-xl">
            <Layers className="h-5 w-5" />
          </span>
          <div className="min-w-0 leading-tight">
            <p className="truncate font-display text-sm font-bold tracking-tight text-ink">
              RDC Accounts Suite
            </p>
            <p className="mt-0.5 truncate text-[11px] font-medium text-ink-faint">
              Finance operations
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onNavigate}
          aria-label="Close navigation"
          aria-controls="primary-navigation"
          className="ml-auto grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-transparent text-ink-dim transition hover:border-border hover:bg-bg-soft hover:text-ink xl:hidden"
        >
          <X className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={onCollapseToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          aria-controls="primary-navigation"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cn(
            'hidden h-11 w-11 shrink-0 place-items-center rounded-xl border border-transparent text-ink-dim transition hover:border-border hover:bg-bg-soft hover:text-accent xl:grid',
            collapsed ? 'mx-auto' : 'ml-auto',
          )}
        >
          {collapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
        </button>
      </div>

      <motion.nav
        className="flex flex-1 flex-col gap-1.5 overflow-y-auto py-1"
        initial={reduceMotion ? false : { opacity: 0, x: -10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.45, delay: 0.08, ease: [0.16, 1, 0.3, 1] }}
      >
        {visibleNav.map((item) => (
          <SidebarLink
            key={item.to}
            item={item}
            end={item.to === '/'}
            collapsed={collapsed}
            onNavigate={onNavigate}
          />
        ))}

        {user?.role === 'admin' && (
          <>
            <div
              className={cn(
                'mt-4 mb-1 px-3 text-xs font-semibold text-ink-faint',
                collapsed && 'xl:hidden',
              )}
            >
              Admin
            </div>
            <SidebarLink
              item={{ to: '/admin/users', label: 'Users', icon: Users }}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
            <SidebarLink
              item={{ to: '/admin/email', label: 'Email administration', icon: Mail }}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
            <SidebarLink
              item={{ to: '/admin/system', label: 'System maintenance', icon: DatabaseBackup }}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
            <SidebarLink
              item={{ to: '/admin/audit-log', label: 'Audit log', icon: ClipboardList }}
              collapsed={collapsed}
              onNavigate={onNavigate}
            />
          </>
        )}

        <div
          className={cn(
            'mt-4 mb-1 px-3 text-xs font-semibold text-ink-faint',
            collapsed && 'xl:hidden',
          )}
        >
          General
        </div>
        <SidebarLink
          item={{ to: '/settings', label: 'Settings', icon: SettingsIcon }}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      </motion.nav>

      <button
        onClick={() => void logout()}
        aria-label="Logout"
        title={collapsed ? 'Logout' : undefined}
        className={cn(
          'flex items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-sm font-medium text-ink-dim transition duration-200 hover:border-border hover:bg-bg-soft/75 hover:text-ink',
          collapsed && 'xl:justify-center xl:px-0',
        )}
      >
        <LogOut className="h-4 w-4 shrink-0" />
        <span className={cn('whitespace-nowrap transition-opacity duration-200', collapsed && 'xl:w-0 xl:overflow-hidden xl:opacity-0')}>
          Logout
        </span>
      </button>
    </motion.aside>
  )
}

function SidebarLink({
  item,
  end,
  collapsed,
  onNavigate,
}: {
  item: NavItem
  end?: boolean
  collapsed?: boolean
  onNavigate?: () => void
}) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={end}
      onClick={onNavigate}
      aria-label={item.label}
      title={item.label}
      className={({ isActive }) =>
        cn(
          'group relative flex min-h-11 items-center gap-3 overflow-hidden rounded-xl border px-3 py-2.5 text-sm font-semibold transition duration-200 hover:translate-x-0.5 active:scale-[0.985]',
          collapsed && 'xl:justify-center xl:px-0',
          isActive
            ? 'border-transparent text-accent'
            : 'border-transparent text-ink-dim hover:border-border hover:bg-bg-soft/75 hover:text-ink',
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <motion.span
              layoutId="sidebar-active-item"
              className="absolute inset-0 rounded-xl border border-accent/15 bg-accent/10 shadow-[inset_3px_0_0_var(--color-accent)]"
              transition={{ type: 'spring', stiffness: 420, damping: 34, mass: 0.72 }}
            />
          )}
          <Icon className="relative h-4 w-4 shrink-0" strokeWidth={1.9} />
          <span
            className={cn(
              'relative truncate whitespace-nowrap transition-opacity duration-200',
              collapsed && 'xl:w-0 xl:overflow-hidden xl:opacity-0',
            )}
          >
            {item.label}
          </span>
        </>
      )}
    </NavLink>
  )
}
