/**
 * Thin fetch wrapper for the backend API.
 *
 * - Always talks to same-origin "/api" (the production build is served by
 *   FastAPI from the same origin; in dev, vite.config.ts proxies /api to the
 *   backend).
 * - Sends cookies (httpOnly session cookie) on every request.
 * - Throws an ApiError with a parsed message on any non-2xx response.
 */

const BASE_PATH = '/api'
const REQUEST_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 10 * 60_000
const REQUESTED_WITH = 'AccountsPayablesSuite'

export const AUTH_EXPIRED_EVENT = 'accounts-payables:auth-expired'

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

async function parseErrorMessage(res: Response): Promise<{ message: string; body: unknown }> {
  const contentType = res.headers.get('content-type') ?? ''
  try {
    if (contentType.includes('application/json')) {
      const body = await res.json()
      const message =
        (typeof body === 'object' && body !== null && 'detail' in body
          ? String((body as { detail: unknown }).detail)
          : undefined) ??
        (typeof body === 'object' && body !== null && 'message' in body
          ? String((body as { message: unknown }).message)
          : undefined) ??
        res.statusText ??
        'Request failed'
      return { message, body }
    }
    const text = await res.text()
    return { message: text || res.statusText || 'Request failed', body: text }
  } catch {
    return { message: res.statusText || 'Request failed', body: undefined }
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${BASE_PATH}${path}`, {
      credentials: 'include',
      ...init,
      signal: init?.signal ?? controller.signal,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.method && init.method !== 'GET' ? { 'X-Requested-With': REQUESTED_WITH } : {}),
        ...init?.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The request timed out. Please try again.')
    }
    throw new ApiError(0, 'Unable to reach the server. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }

  if (!res.ok) {
    const { message, body } = await parseErrorMessage(res)
    if (res.status === 401 && path !== '/auth/login') {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
    throw new ApiError(res.status, message, body)
  }

  if (res.status === 204) {
    return undefined as T
  }

  const contentType = res.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await res.json()) as T
  }
  return undefined as T
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET' })
}

export function post<T>(path: string, data?: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      body: data !== undefined ? JSON.stringify(data) : undefined,
    },
    timeoutMs,
  )
}

/** POST JSON and return a binary response (ZIP/PDF/etc.). */
export async function postBlob(path: string, data?: unknown): Promise<Blob> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${BASE_PATH}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': REQUESTED_WITH,
      },
      body: data !== undefined ? JSON.stringify(data) : undefined,
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The download timed out. Please try again.')
    }
    throw new ApiError(0, 'Unable to reach the server. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }

  if (!res.ok) {
    const { message, body } = await parseErrorMessage(res)
    if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new ApiError(res.status, message, body)
  }
  return res.blob()
}

export function put<T>(path: string, data?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: data !== undefined ? JSON.stringify(data) : undefined,
  })
}

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}

/**
 * POST a FormData payload (for file uploads) without forcing a JSON
 * Content-Type header (the browser sets the multipart boundary itself).
 */
export async function postForm<T>(path: string, formData: FormData): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${BASE_PATH}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-Requested-With': REQUESTED_WITH },
      body: formData,
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The upload timed out. Please try again.')
    }
    throw new ApiError(0, 'Unable to reach the server. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }

  if (!res.ok) {
    const { message, body } = await parseErrorMessage(res)
    if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new ApiError(res.status, message, body)
  }

  return (await res.json()) as T
}

/**
 * Build the same-origin URL for a file download/streaming endpoint, for use
 * in an <a href> or window.location assignment.
 */
export function apiUrl(path: string): string {
  return `${BASE_PATH}${path}`
}
