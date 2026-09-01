export const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}
  if (!response.ok) {
    const detail = payload?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.problems?.join('; ') ?? detail?.detail ?? detail?.code ?? `API ${response.status}`
    throw new Error(message)
  }
  return payload.data as T
}

export const getJson = <T,>(path: string) => request<T>(path)
export const postJson = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
export const putJson = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const deleteJson = <T,>(path: string) => request<T>(path, { method: 'DELETE' })
