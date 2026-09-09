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
    const d = detail as { problems?: string[]; detail?: string; reason?: string; code?: string }
    if (d.problems?.length) return d.problems.join('; ')
    if (d.detail) return d.detail
    /* `reason` is the shape fifteen endpoints in `control.py` actually use --
     * `{"code": "market_data_unavailable", "reason": "<what went wrong>"}` --
     * and it was the one field this reader did not know about. Every one of
     * them fell through to `code`, so the server wrote an explanation and the
     * interface displayed a machine token instead: the chart's empty state
     * read "Market data unavailable" and then, underneath it,
     * "market_data_unavailable". The code stays on `ApiError.payload` for
     * anything that wants to branch on it. */
    if (d.reason) return d.reason
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

  /* Not every response is JSON, and the ones that are not are exactly the ones
   * worth reporting well. A proxy's HTML error page, a gateway timeout with an
   * empty body, a crashed worker returning a stack trace as plain text: each
   * used to throw a SyntaxError out of `JSON.parse` *before* the status was
   * ever read. That error is not an ApiError, so every caller's handling of it
   * was bypassed and the user saw "Unexpected token '<'" with no status
   * attached and nothing to act on.
   *
   * Parse defensively, keep the raw body as the payload when it will not
   * parse, and let the status carry the message. */
  let payload: unknown = {}
  let parsed = true
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      parsed = false
      payload = text
    }
  }

  if (!response.ok) {
    const message = parsed
      ? readMessage(payload, response.status)
      : `API ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`
    throw new ApiError(message, response.status, payload)
  }
  if (!parsed) {
    throw new ApiError(
      `API ${response.status} returned a body that is not JSON`,
      response.status,
      payload,
    )
  }
  return (payload as { data?: unknown }).data as T
}

export const getJson = <T,>(path: string) => request<T>(path)
export const postJson = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
export const patchJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const putJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const deleteJson = <T,>(path: string) => request<T>(path, { method: 'DELETE' })
