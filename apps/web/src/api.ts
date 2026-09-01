export const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8765/api/v1'

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`)
  if (!response.ok) throw new Error(`API ${response.status}: ${path}`)
  const envelope = await response.json()
  return envelope.data as T
}
