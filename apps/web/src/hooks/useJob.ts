import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson, postJson } from '../api'
import type { Job } from '../types'

/** How many consecutive failed polls to absorb before giving up on a job.
 *
 * A local API restarting, a dropped socket, one 502 from a proxy: none of those
 * mean the job died, and the job is still running on the server whatever the
 * client thinks. Retrying a few times with a widening gap covers a restart
 * without hiding an API that is genuinely gone. */
const MAX_CONSECUTIVE_FAILURES = 5

export function useJob() {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  /* Which poll loop currently owns this hook.
   *
   * `stop()` can clear a pending timeout but cannot cancel a request already in
   * flight, and that gap was a real defect: starting a second job while the
   * first one's fetch was outstanding left both loops alive. The stale one
   * resolved, wrote its own job into state, and rescheduled itself into the
   * same ref — so the bar flipped between two jobs and the newer one's timer
   * handle was lost, leaving cancel and clear able to reach only one of them.
   *
   * Every loop captures the generation it was started under and does nothing at
   * all once that number has moved on. */
  const generation = useRef(0)

  const stop = useCallback(() => {
    generation.current += 1
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const poll = useCallback((jobId: string, since: number) => {
    const mine = generation.current
    let failures = 0

    const tick = async () => {
      if (generation.current !== mine) return
      try {
        const next = await getJson<Job>(`/jobs/${jobId}`)
        if (generation.current !== mine) return
        failures = 0
        setError(null)
        setJob(next)
        if (next.status === 'RUNNING' || next.status === 'QUEUED') {
          const age = Date.now() - since
          const delay = age > 60_000 ? 2500 : age > 15_000 ? 1200 : 600
          timer.current = window.setTimeout(tick, delay)
        }
      } catch (e) {
        if (generation.current !== mine) return
        failures += 1
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          /* Stop and say so. Retrying forever would leave an unreachable API
           * behind a progress bar that never settles, which is the failure this
           * whole component exists to prevent. */
          setError((e as Error).message)
          return
        }
        // Widen the gap on each attempt: 400ms, 800ms, 1.6s, 3.2s.
        timer.current = window.setTimeout(tick, 400 * 2 ** (failures - 1))
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
