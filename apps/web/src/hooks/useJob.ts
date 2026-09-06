import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson, postJson } from '../api'
import type { Job } from '../types'

/** Polls one background job until it settles.
 *
 * Polling rather than a socket: the backend is a single local process and a
 * one-second poll costs nothing, while a socket would add a reconnection
 * lifecycle for no benefit on localhost. The interval eases off once a job has
 * been running a while, because a sixteen-year backtest does not need 300
 * requests to tell you it is still going.
 */
export function useJob() {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const stop = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const poll = useCallback((jobId: string, since: number) => {
    const tick = async () => {
      try {
        const next = await getJson<Job>(`/jobs/${jobId}`)
        setJob(next)
        if (next.status === 'RUNNING' || next.status === 'QUEUED') {
          const age = Date.now() - since
          const delay = age > 60_000 ? 2500 : age > 15_000 ? 1200 : 600
          timer.current = window.setTimeout(tick, delay)
        }
      } catch (e) {
        setError((e as Error).message)
      }
    }
    void tick()
  }, [])

  const start = useCallback(
    (started: Job) => {
      stop()
      setError(null)
      setJob(started)
      poll(started.job_id, Date.now())
    },
    [poll, stop],
  )

  const cancel = useCallback(async () => {
    if (!job) return
    try {
      await postJson(`/jobs/${job.job_id}/cancel`)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [job])

  const clear = useCallback(() => {
    stop()
    setJob(null)
    setError(null)
  }, [stop])

  useEffect(() => stop, [stop])

  const active = job?.status === 'RUNNING' || job?.status === 'QUEUED'
  return { job, error, active, start, cancel, clear }
}

/** Seconds as a compact duration. `92` reads as `1m 32s`, not `92.000s`. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}
