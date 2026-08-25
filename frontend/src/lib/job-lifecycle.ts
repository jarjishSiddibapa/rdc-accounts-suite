import { getCurrentTabId } from '@/lib/tab-session'

const BASE_PATH = '/api'
const REQUESTED_WITH = 'AccountsPayablesSuite'
const activeJobs = new Map<string, number>()
let lifecycleListenersInstalled = false

function sendAbandon(jobId: string) {
  const tabId = getCurrentTabId()
  if (!tabId) return
  void fetch(`${BASE_PATH}/jobs/${encodeURIComponent(jobId)}/abandon`, {
    method: 'POST',
    credentials: 'include',
    keepalive: true,
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': REQUESTED_WITH,
      'X-Client-Tab-ID': tabId,
    },
    body: JSON.stringify({ tab_id: tabId }),
  }).catch(() => {
    // Browser shutdown can interrupt even a keepalive request. The server's
    // client-heartbeat expiry is the durable fallback for that case.
  })
}

function abandonEveryActiveJob() {
  for (const jobId of activeJobs.keys()) sendAbandon(jobId)
  activeJobs.clear()
}

function installLifecycleListeners() {
  if (lifecycleListenersInstalled) return
  lifecycleListenersInstalled = true
  window.addEventListener('pagehide', abandonEveryActiveJob)
  window.addEventListener('beforeunload', abandonEveryActiveJob)
}

/**
 * Register one rendered progress panel as the live consumer of a job.
 * The zero-delay cleanup absorbs React StrictMode's development-only
 * mount/unmount/remount probe without accidentally abandoning real work.
 */
export function registerTabOwnedJob(jobId: string): () => void {
  installLifecycleListeners()
  activeJobs.set(jobId, (activeJobs.get(jobId) ?? 0) + 1)
  return () => {
    window.setTimeout(() => {
      const remaining = (activeJobs.get(jobId) ?? 0) - 1
      if (remaining > 0) {
        activeJobs.set(jobId, remaining)
        return
      }
      if (activeJobs.delete(jobId)) sendAbandon(jobId)
    }, 0)
  }
}

export function settleTabOwnedJob(jobId: string) {
  activeJobs.delete(jobId)
}
