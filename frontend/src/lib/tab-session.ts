const TAB_KEY_PREFIX = 'rdc-accounts-suite:active-tab:'
const TAB_ID_KEY = 'rdc-accounts-suite:tab-id'
const APP_OPENED_KEY = 'rdc-accounts-suite:has-opened'
const LOGOUT_SYNC_KEY = 'rdc-accounts-suite:logout-sync'
const LAST_ACTIVITY_KEY = 'rdc-accounts-suite:last-activity'
const LAST_SERVER_TOUCH_KEY = 'rdc-accounts-suite:last-server-touch'
const CHANNEL_NAME = 'rdc-accounts-suite:tabs'
const HEARTBEAT_MS = 4_000
const TAB_STALE_MS = 15_000
const PROBE_WAIT_MS = 250
const IDLE_TIMEOUT_MS = 30 * 60_000
const IDLE_WARNING_LEAD_MS = 60_000
const ACTIVITY_WRITE_THROTTLE_MS = 1_000
const SESSION_TOUCH_THROTTLE_MS = 10_000

type ChannelMessage =
  | { type: 'probe'; probeId: string }
  | { type: 'pong'; probeId: string; tabId: string }
  | { type: 'logout' }

let tabId = ''
let heartbeatId: number | null = null
let channel: BroadcastChannel | null = null
let preparePromise: Promise<boolean> | null = null
const logoutListeners = new Set<() => void>()
const probeResolvers = new Map<string, () => void>()
let idleSessionCleanup: (() => void) | null = null

function createId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function safeGet(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(storage: Storage, key: string, value: string) {
  try {
    storage.setItem(key, value)
  } catch {
    // Storage may be unavailable in privacy modes. The current tab still works.
  }
}

function safeRemove(storage: Storage, key: string) {
  try {
    storage.removeItem(key)
  } catch {
    // Ignore unavailable storage.
  }
}

function storedTimestamp(key: string): number | null {
  const value = Number(safeGet(window.localStorage, key))
  return Number.isFinite(value) && value > 0 ? value : null
}

function activeTabIds(): string[] {
  const now = Date.now()
  const active: string[] = []
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index)
      if (!key?.startsWith(TAB_KEY_PREFIX)) continue
      const seenAt = Number(window.localStorage.getItem(key))
      if (Number.isFinite(seenAt) && now - seenAt <= TAB_STALE_MS) {
        active.push(key.slice(TAB_KEY_PREFIX.length))
      } else {
        window.localStorage.removeItem(key)
      }
    }
  } catch {
    return []
  }
  return active
}

function touchTab() {
  if (tabId) safeSet(window.localStorage, `${TAB_KEY_PREFIX}${tabId}`, String(Date.now()))
}

function unregisterTab() {
  if (heartbeatId !== null) {
    window.clearInterval(heartbeatId)
    heartbeatId = null
  }
  if (tabId) safeRemove(window.localStorage, `${TAB_KEY_PREFIX}${tabId}`)
}

function startHeartbeat() {
  touchTab()
  if (heartbeatId === null) heartbeatId = window.setInterval(touchTab, HEARTBEAT_MS)
}

function notifyLogoutListeners() {
  logoutListeners.forEach((listener) => listener())
}

function setupChannel() {
  if (channel || !('BroadcastChannel' in window)) return
  channel = new BroadcastChannel(CHANNEL_NAME)
  channel.addEventListener('message', (event: MessageEvent<ChannelMessage>) => {
    const message = event.data
    if (message?.type === 'probe') {
      channel?.postMessage({ type: 'pong', probeId: message.probeId, tabId } satisfies ChannelMessage)
    } else if (message?.type === 'pong') {
      probeResolvers.get(message.probeId)?.()
    } else if (message?.type === 'logout') {
      notifyLogoutListeners()
    }
  })
}

async function anotherTabResponds(candidateIds: string[]): Promise<boolean> {
  if (candidateIds.length === 0) return false
  if (!channel) return true // Heartbeat fallback for browsers without BroadcastChannel.

  const probeId = createId()
  return await new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (result: boolean) => {
      if (settled) return
      settled = true
      probeResolvers.delete(probeId)
      resolve(result)
    }
    probeResolvers.set(probeId, () => finish(true))
    channel?.postMessage({ type: 'probe', probeId } satisfies ChannelMessage)
    window.setTimeout(() => finish(false), PROBE_WAIT_MS)
  })
}

async function initializeTabSession(): Promise<boolean> {
  setupChannel()
  const existingId = safeGet(window.sessionStorage, TAB_ID_KEY)
  const navigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
  const activeIds = activeTabIds()
  const looksDuplicated = Boolean(
    existingId && activeIds.includes(existingId) && navigation?.type === 'navigate',
  )
  const returningTab = Boolean(existingId && !looksDuplicated)

  tabId = returningTab && existingId ? existingId : createId()
  safeSet(window.sessionStorage, TAB_ID_KEY, tabId)

  const hasOpenedBefore = safeGet(window.localStorage, APP_OPENED_KEY) === 'true'
  const candidateIds = activeIds.filter((id) => id !== tabId)
  const anotherTabIsOpen = returningTab ? true : await anotherTabResponds(candidateIds)
  const requireFreshLogin = hasOpenedBefore && !returningTab && !anotherTabIsOpen

  safeSet(window.localStorage, APP_OPENED_KEY, 'true')
  startHeartbeat()

  window.addEventListener('pagehide', unregisterTab)
  window.addEventListener('beforeunload', unregisterTab)
  window.addEventListener('pageshow', startHeartbeat)
  document.addEventListener('visibilitychange', touchTab)

  return requireFreshLogin
}

/**
 * Returns true when this is a fresh tab and no other live application tab
 * exists. The auth provider then clears the previous server session before
 * attempting to restore a user.
 */
export function prepareTabSession(): Promise<boolean> {
  preparePromise ??= initializeTabSession()
  return preparePromise
}

/** Stable identity for this physical browser tab (never shared via localStorage). */
export function getCurrentTabId(): string {
  return tabId || safeGet(window.sessionStorage, TAB_ID_KEY) || ''
}

export function announceLogout() {
  channel?.postMessage({ type: 'logout' } satisfies ChannelMessage)
  safeSet(window.localStorage, LOGOUT_SYNC_KEY, String(Date.now()))
}

export function subscribeToTabLogout(listener: () => void): () => void {
  logoutListeners.add(listener)
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOGOUT_SYNC_KEY) listener()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    logoutListeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

/**
 * Keep the 30-minute inactivity window shared across every open suite tab.
 * Only deliberate user interaction counts; the tab heartbeat and background
 * job polling do not keep an unattended session alive. `onIdleWarning` fires
 * once, one minute before the auto-logout, so the UI can show a heads-up;
 * `onIdleWarningClear` fires whenever activity pushes the deadline back out,
 * so that warning can be dismissed again.
 */
export function startIdleSession(
  onIdle: () => void,
  onSessionTouch: () => void | Promise<void>,
  onIdleWarning: () => void,
  onIdleWarningClear: () => void,
): () => void {
  idleSessionCleanup?.()

  let stopped = false
  let idleTimer: number | null = null
  let warningTimer: number | null = null
  let lastLocalWrite = 0

  const stop = () => {
    if (stopped) return
    stopped = true
    if (idleTimer !== null) window.clearTimeout(idleTimer)
    idleTimer = null
    if (warningTimer !== null) window.clearTimeout(warningTimer)
    warningTimer = null
    for (const eventName of activityEvents) {
      window.removeEventListener(eventName, recordActivity)
    }
    document.removeEventListener('visibilitychange', handleVisibility)
    window.removeEventListener('storage', handleStorage)
    if (idleSessionCleanup === stop) idleSessionCleanup = null
  }

  const expireIfIdle = () => {
    if (stopped) return
    const lastActivity = storedTimestamp(LAST_ACTIVITY_KEY) ?? Date.now()
    const remaining = IDLE_TIMEOUT_MS - (Date.now() - lastActivity)
    if (remaining > 0) {
      idleTimer = window.setTimeout(expireIfIdle, remaining)
      return
    }
    stop()
    onIdle()
  }

  const scheduleExpiry = () => {
    if (idleTimer !== null) window.clearTimeout(idleTimer)
    if (warningTimer !== null) window.clearTimeout(warningTimer)
    onIdleWarningClear()
    const lastActivity = storedTimestamp(LAST_ACTIVITY_KEY) ?? Date.now()
    const remaining = Math.max(0, IDLE_TIMEOUT_MS - (Date.now() - lastActivity))
    idleTimer = window.setTimeout(expireIfIdle, remaining)
    const warningDelay = remaining - IDLE_WARNING_LEAD_MS
    if (warningDelay > 0) {
      warningTimer = window.setTimeout(onIdleWarning, warningDelay)
    } else if (remaining > 0) {
      onIdleWarning()
    }
  }

  const touchServerSession = (now: number) => {
    const lastServerTouch = storedTimestamp(LAST_SERVER_TOUCH_KEY) ?? 0
    if (now - lastServerTouch < SESSION_TOUCH_THROTTLE_MS) return

    // Claim the shared touch slot before the request so simultaneous tabs do
    // not create a burst of identical refresh calls.
    safeSet(window.localStorage, LAST_SERVER_TOUCH_KEY, String(now))
    void Promise.resolve(onSessionTouch()).catch(() => {
      // The API wrapper handles an expired session globally. A temporary
      // network failure must not manufacture activity or crash the page.
    })
  }

  function recordActivity() {
    if (stopped) return
    const now = Date.now()
    if (now - lastLocalWrite < ACTIVITY_WRITE_THROTTLE_MS) return
    lastLocalWrite = now
    safeSet(window.localStorage, LAST_ACTIVITY_KEY, String(now))
    scheduleExpiry()
    touchServerSession(now)
  }

  function handleVisibility() {
    if (document.visibilityState === 'visible') recordActivity()
  }

  function handleStorage(event: StorageEvent) {
    if (event.key === LAST_ACTIVITY_KEY) scheduleExpiry()
  }

  const activityEvents = ['pointerdown', 'keydown', 'scroll', 'touchstart'] as const
  const now = Date.now()
  safeSet(window.localStorage, LAST_ACTIVITY_KEY, String(now))
  touchServerSession(now)
  for (const eventName of activityEvents) {
    window.addEventListener(eventName, recordActivity, { passive: true })
  }
  document.addEventListener('visibilitychange', handleVisibility)
  window.addEventListener('storage', handleStorage)
  scheduleExpiry()

  idleSessionCleanup = stop
  return stop
}

export function clearIdleSessionState() {
  idleSessionCleanup?.()
  safeRemove(window.localStorage, LAST_ACTIVITY_KEY)
  safeRemove(window.localStorage, LAST_SERVER_TOUCH_KEY)
}
