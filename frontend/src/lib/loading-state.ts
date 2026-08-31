export const GLOBAL_LOADING_MESSAGE = 'One sec… pretending this is very complicated 😎'

type Listener = () => void

let pendingRequests = 0
const listeners = new Set<Listener>()

function emit(): void {
  listeners.forEach((listener) => listener())
}

export function beginGlobalLoading(): () => void {
  pendingRequests += 1
  emit()
  let finished = false
  return () => {
    if (finished) return
    finished = true
    pendingRequests = Math.max(0, pendingRequests - 1)
    emit()
  }
}

export function subscribeGlobalLoading(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getGlobalLoadingSnapshot(): boolean {
  return pendingRequests > 0
}
