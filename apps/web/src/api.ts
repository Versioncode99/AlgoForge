export const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

/** An API failure that keeps the server's payload attached.
 *
 * The message alone throws away everything useful. A refused prop matrix
 * carries the list of strategies it skipped and why; a validation error carries
 * which field was wrong. Views that want that detail can reach for it, and
 * views that do not still get a readable message.
 */
export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  /** The `detail` object when the server sent one, otherwise undefined. */
  detail<T = Record<string, unknown>>(): T | undefined {
    const body = this.payload as { detail?: unknown } | undefined
    const detail = body?.detail
    return detail && typeof detail === 'object' && !Array.isArray(detail)
      ? (detail as T)
      : undefined
  }
}

/** Turn any error shape FastAPI can produce into one readable line.
 *
 * Pydantic rejects a bad request body with an *array* of field errors, which
 * the previous handler could not read at all — it fell through to a bare
 * "API 422" with nothing about which field was wrong or why.
 */
function readMessage(payload: unknown, status: number): string {
  const body = payload as { detail?: unknown } | undefined
  const detail = body?.detail

  if (typeof detail === 'string') return detail

  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        const e = item as { loc?: unknown[]; msg?: string }
        const field = Array.isArray(e.loc) ? e.loc.filter((p) => p !== 'body').join('.') : ''
        return field ? `${field}: ${e.msg ?? 'invalid'}` : (e.msg ?? 'invalid')
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }

  if (detail && typeof detail === 'object') {
    const d = detail as { problems?: string[]; detail?: string; code?: string }
    if (d.problems?.length) return d.problems.join('; ')
    if (d.detail) return d.detail
    if (d.code) return d.code
  }

  return `API ${status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}
  if (!response.ok) {
    throw new ApiError(readMessage(payload, response.status), response.status, payload)
  }
  return payload.data as T
}

export const getJson = <T,>(path: string) => request<T>(path)
export const postJson = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
export const patchJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const putJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const deleteJson = <T,>(path: string) => request<T>(path, { method: 'DELETE' })
