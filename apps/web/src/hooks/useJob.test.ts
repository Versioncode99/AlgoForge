/* The poller is the only thing standing between a running backtest and an
 * interface that looks hung. Both cases here were live defects, and neither
 * looks broken from the outside — the bar simply stops telling the truth. */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useJob } from './useJob'
import type { Job } from '../types'

const job = (id: string, status: Job['status'], done = 0): Job => ({
  job_id: id,
  kind: 'backtest',
  label: 'Backtest',
  status,
  total: 100,
  done,
  note: '',
  fraction: done / 100,
  elapsed_seconds: 1,
  eta_seconds: null,
  error: null,
  created_at: 0,
})

/** Queue of responses `fetch` will hand back, in order, per job id. */
let responses: Map<string, Array<Job | Error>>
let requested: string[]

beforeEach(() => {
  responses = new Map()
  requested = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const id = String(url).split('/jobs/')[1]
      requested.push(id)
      const queue = responses.get(id) ?? []
      const next = queue.length > 1 ? queue.shift()! : (queue[0] ?? job(id, 'DONE'))
      if (next instanceof Error) throw next
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify({ data: next }),
      } as Response
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('useJob', () => {
  it('keeps polling after a request fails', async () => {
    /* One dropped request used to end the poll for good. The `catch` recorded
     * the error and scheduled nothing, so a job that was still running on the
     * server left the bar frozen at its last known percentage — with `active`
     * still true, so the sweep kept animating over a number that would never
     * move again. Restarting the API, or any blip on the socket, was enough. */
    responses.set('a', [
      new Error('connection reset'),
      job('a', 'RUNNING', 40),
      job('a', 'DONE', 100),
    ])

    const { result } = renderHook(() => useJob())
    act(() => result.current.start(job('a', 'QUEUED')))

    await waitFor(() => expect(result.current.job?.status).toBe('DONE'), { timeout: 5000 })
    expect(result.current.active).toBe(false)
  })

  it('does not let a superseded job keep writing state', async () => {
    /* `stop()` clears the pending timeout but cannot cancel a request already
     * in flight. Starting a second job while the first one's fetch was
     * outstanding left both loops alive: the old one resolved, called setJob
     * with its own state, and rescheduled itself into the same ref. The bar
     * then flipped between two jobs and the newer one's handle was lost, so
     * cancelling reached only one of them. */
    let releaseFirst: (() => void) | undefined
    const firstInFlight = new Promise<void>(resolve => {
      releaseFirst = resolve
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const id = String(url).split('/jobs/')[1]
        requested.push(id)
        if (id === 'old') await firstInFlight
        const payload = id === 'old' ? job('old', 'RUNNING', 10) : job('new', 'RUNNING', 90)
        return {
          ok: true,
          status: 200,
          statusText: 'OK',
          text: async () => JSON.stringify({ data: payload }),
        } as Response
      }),
    )

    const { result } = renderHook(() => useJob())
    act(() => result.current.start(job('old', 'QUEUED')))
    act(() => result.current.start(job('new', 'QUEUED')))

    // Let the superseded request land now that the second job owns the hook.
    await act(async () => {
      releaseFirst?.()
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.job?.done).toBe(90))

    // Give the abandoned loop every chance to reschedule and overwrite.
    await new Promise(resolve => setTimeout(resolve, 1200))
    expect(result.current.job?.job_id).toBe('new')
    expect(requested.filter(id => id === 'old').length).toBe(1)
  })

  it('gives up and reports when the failures do not stop', async () => {
    // Retrying forever would hide a genuinely unreachable API behind a bar
    // that never settles. It has to stop and say so.
    responses.set('c', [new Error('gone')])

    const { result } = renderHook(() => useJob())
    act(() => result.current.start(job('c', 'QUEUED')))

    await waitFor(() => expect(result.current.error).toBeTruthy(), { timeout: 8000 })
    expect(result.current.error).toContain('gone')
  })
})
