import { useEffect, useState, type ReactNode } from 'react'
import { Sidebar } from '@/components/Sidebar'
import { TopBar } from '@/components/TopBar'
import { Footer } from '@/components/Footer'
import { SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH } from '@/lib/sidebarLayout'

const SIDEBAR_COLLAPSED_KEY = 'accounts-suite-sidebar-collapsed'
const SIDEBAR_WIDTH_KEY = 'accounts-suite-sidebar-width'

function getSavedSidebarState(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
  } catch {
    return false
  }
}

function getSavedSidebarWidth(): number {
  try {
    const raw = Number(window.localStorage.getItem(SIDEBAR_WIDTH_KEY))
    if (Number.isFinite(raw) && raw >= SIDEBAR_MIN_WIDTH && raw <= SIDEBAR_MAX_WIDTH) return raw
  } catch {
    // ignore
  }
  return SIDEBAR_DEFAULT_WIDTH
}

export function AppShell({ title, children }: { title: string; children: ReactNode }) {
  const [navigationOpen, setNavigationOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getSavedSidebarState)
  const [sidebarWidth, setSidebarWidth] = useState(getSavedSidebarWidth)

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed))
    } catch {
      // Storage can be disabled by browser privacy settings; the toggle still works for this page.
    }
  }, [sidebarCollapsed])

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth))
    } catch {
      // Storage can be disabled by browser privacy settings; the resize still works for this page.
    }
  }, [sidebarWidth])

  useEffect(() => {
    if (!navigationOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setNavigationOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [navigationOpen])

  return (
    <div className="app-shell-frame relative flex min-w-0 flex-1">
      <button
        type="button"
        aria-label="Close navigation"
        onClick={() => setNavigationOpen(false)}
        className={`fixed inset-0 z-30 bg-[#090b18]/55 backdrop-blur-md transition-opacity xl:hidden ${
          navigationOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
      />
      <Sidebar
        mobileOpen={navigationOpen}
        collapsed={sidebarCollapsed}
        onCollapseToggle={() => setSidebarCollapsed((current) => !current)}
        width={sidebarWidth}
        onWidthChange={setSidebarWidth}
        onNavigate={() => setNavigationOpen(false)}
        className="app-sidebar"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-4 xl:gap-5">
        <TopBar title={title} onMenuClick={() => setNavigationOpen(true)} />
        <main id="main-content" className="page-enter min-w-0 w-full flex-1 pb-4">{children}</main>
        <Footer />
      </div>
    </div>
  )
}
