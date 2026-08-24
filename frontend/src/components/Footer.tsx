import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from '@/lib/auth-context'
import { get } from '@/lib/api'

// Mirrors the /tools/* routes in App.tsx: which app key owns each tool page,
// so the footer can credit that app's specific collaborator instead of the
// suite-wide default. Kept local rather than shared with Sidebar/Dashboard's
// own nav lists, matching how those two already duplicate this pairing.
const TOOL_APP_KEYS: Record<string, string> = {
  '/tools/erp-converter': 'erp-to-excel',
  '/tools/rdc-payables': 'rdc-payables',
  '/tools/unaccounted-transactions': 'unaccounted',
  '/tools/trial-balance': 'trial-balance',
  '/tools/gstr2b-combinator': 'gstr2b-combinator',
  '/tools/unapplied-receipts': 'unapplied-receipts',
  '/tools/ultrafine-balance-confirmation': 'ultrafine-balance-confirmation',
  '/tools/ultrafine-payment-reminder': 'ultrafine-payment-reminder',
}

export function Footer() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const [credits, setCredits] = useState<Record<string, string | null>>({})

  useEffect(() => {
    if (!user) return
    let cancelled = false
    get<Record<string, string | null>>('/admin/apps/credits')
      .then((data) => {
        if (!cancelled) setCredits(data)
      })
      .catch(() => {
        // Footer stays on the suite-wide default credit if this fails.
      })
    return () => {
      cancelled = true
    }
  }, [user])

  const appKey = TOOL_APP_KEYS[pathname]
  const collaborator = appKey ? credits[appKey] : null

  return (
    <footer className="px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] text-center text-[11px] font-medium tracking-wide text-ink-faint">
      RDC Accounts Suite · {collaborator ? `Made by Jarjish & ${collaborator}` : 'Made with ❤️ by Jarjish'}
    </footer>
  )
}
