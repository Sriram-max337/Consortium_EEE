export const API_URL = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly kind: 'network' | 'http' | 'malformed_response'

  constructor(message: string, status: number, kind: 'network' | 'http' | 'malformed_response') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.kind = kind
  }
}

function responseErrorMessage(body: unknown, status: number): string {
  if (!body || typeof body !== 'object' || !('detail' in body)) return `The API returned HTTP ${status}.`
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const structured = detail as { message?: unknown; detail?: unknown; issues?: unknown }
    const message = typeof structured.message === 'string'
      ? structured.message
      : typeof structured.detail === 'string'
        ? structured.detail
        : null
    const issues = Array.isArray(structured.issues)
      ? structured.issues.filter((issue): issue is string => typeof issue === 'string')
      : []
    if (message || issues.length) return [message, ...issues].filter(Boolean).join(' ')
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (!item || typeof item !== 'object') return null
      const error = item as { loc?: unknown[]; msg?: unknown }
      const path = Array.isArray(error.loc) ? error.loc.filter((part) => part !== 'body').join('.') : ''
      const message = typeof error.msg === 'string' ? error.msg : null
      return message ? `${path ? `${path}: ` : ''}${message}` : null
    }).filter((message): message is string => message !== null)
    if (messages.length) return messages.join(' · ')
  }
  return `The API returned HTTP ${status}.`
}

export async function apiRequest<T>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  accessToken: string,
  body?: unknown,
): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 15_000)
  try {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'application/json',
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) {
      const errorBody = await response.json().catch(() => null) as unknown
      throw new ApiError(responseErrorMessage(errorBody, response.status), response.status, 'http')
    }
    if (response.status === 204) return undefined as T
    return await response.json() as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof SyntaxError) {
      throw new ApiError('The API returned a malformed response.', 200, 'malformed_response')
    }
    const timedOut = error instanceof DOMException && error.name === 'AbortError'
    throw new ApiError(
      timedOut ? 'The API request timed out. Please try again.' : 'Could not reach the API. Check that the backend is running and try again.',
      0,
      'network',
    )
  } finally {
    window.clearTimeout(timeout)
  }
}

export function apiGet<T>(path: string, accessToken: string): Promise<T> {
  return apiRequest<T>('GET', path, accessToken)
}
