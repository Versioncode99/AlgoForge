/* What the interface tells a person when a request fails.
 *
 * Every case here was reachable from a running application, and each one used
 * to surface something the reader could do nothing with. */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getJson } from './api'

function respond(status: number, body: string, ok = false, statusText = '') {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok, status, statusText, text: async () => body }) as Response),
  )
}

afterEach(() => vi.unstubAllGlobals())

async function failure(status: number, body: string, statusText = ''): Promise<ApiError> {
  respond(status, body, false, statusText)
  try {
    await getJson('/anything')
  } catch (error) {
    return error as ApiError
  }
  throw new Error('expected the request to reject')
}

describe('API error messages', () => {
  it('prefers the reason the server wrote over its machine code', async () => {
    /* Fifteen endpoints in control.py answer with this exact shape. The reader
     * knew about `detail`, `problems` and `code` but not `reason`, so all of
     * them fell through to the code — the chart's empty state printed the
     * heading "Market data unavailable" and then the token
     * "market_data_unavailable" directly underneath it. */
    const error = await failure(
      409,
      JSON.stringify({
        detail: {
          code: 'market_data_unavailable',
          reason: "MNQ 1m archive is not present; import it before charting.",
        },
      }),
    )
    expect(error.message).toBe("MNQ 1m archive is not present; import it before charting.")
    // The code is still there for anything that wants to branch on it.
    expect(error.detail<{ code: string }>()?.code).toBe('market_data_unavailable')
  })

  it('falls back to the code when there is no reason', async () => {
    const error = await failure(409, JSON.stringify({ detail: { code: 'action_refused' } }))
    expect(error.message).toBe('action_refused')
  })

  it('reads a pydantic field-error array', async () => {
    const error = await failure(
      422,
      JSON.stringify({
        detail: [{ loc: ['body', 'objective'], msg: 'String should have at least 8 characters' }],
      }),
    )
    expect(error.message).toBe('objective: String should have at least 8 characters')
  })

  it('keeps the status when the body is not JSON at all', async () => {
    // A proxy's HTML error page. This used to throw a SyntaxError out of
    // JSON.parse, which is not an ApiError, so every caller's handling of it
    // was bypassed and the reader saw "Unexpected token '<'".
    const error = await failure(502, '<html><body>Bad Gateway</body></html>', 'Bad Gateway')
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(502)
    expect(error.message).toBe('API 502 Bad Gateway')
  })

  it('refuses a 200 whose body is not JSON rather than returning undefined', async () => {
    respond(200, 'not json at all', true)
    await expect(getJson('/anything')).rejects.toBeInstanceOf(ApiError)
  })
})
