import { useCallback, useEffect, useRef, useState } from 'react'
import { API, postJson } from './api'

/* A chat turn in flight, as the interface sees it.
 *
 * The old shape was one mutation: press Send, wait, get a whole reply. It could
 * not show the question before the answer, could not be stopped, and lost all
 * knowledge of a turn if the window reloaded while it ran.
 *
 * This reads `forge_api.chat_runs`'s event stream instead. The events are
 * buffered server-side with an index, so reattaching after a reload replays the
 * run from the beginning rather than joining halfway and showing a fragment.
 */

export type RunStatus = 'idle' | 'accepted' | 'running' | 'completed' | 'cancelled' | 'failed'

export type RunEvent = {
  index: number
  event: string
  data: Record<string, unknown>
}

export type RunState = {
  runId: string | null
  status: RunStatus
  /** What it is doing, in the operator's words. Never its reasoning. */
  stage: string
  /** The answer so far. One piece today; see `chat_runs`'s docstring. */
  text: string
  error: string
  /** The finished turn, exactly as the conversation stores it. */
  turn: Record<string, unknown> | null
  elapsedMs: number
}

const IDLE: RunState = {
  runId: null,
  status: 'idle',
  stage: '',
  text: '',
  error: '',
  turn: null,
  elapsedMs: 0,
}

function apply(state: RunState, event: RunEvent): RunState {
  const data = event.data as Record<string, string | number | undefined>
  switch (event.event) {
    case 'accepted':
      return { ...state, runId: String(data.run_id ?? ''), status: 'accepted' }
    case 'status':
      return { ...state, status: 'running', stage: String(data.detail ?? '') }
    case 'delta':
      return { ...state, status: 'running', text: state.text + String(data.text ?? '') }
    case 'completed':
      return {
        ...state,
        status: 'completed',
        stage: '',
        turn: (event.data.turn as Record<string, unknown>) ?? null,
        elapsedMs: Number(data.elapsed_ms ?? 0),
      }
    case 'cancelled':
      return {
        ...state,
        status: 'cancelled',
        stage: '',
        error: String(data.reason ?? 'stopped'),
        turn: (event.data.turn as Record<string, unknown>) ?? null,
      }
    case 'failed':
      return { ...state, status: 'failed', stage: '', error: String(data.reason ?? 'failed') }
    default:
      // `keep-alive`, and anything a later build adds. An unknown event must
      // never be an error: a client that refused to render a stream it did not
      // fully understand would break on every protocol addition.
      return state
  }
}

/** Parse one SSE frame. Returns null for a comment or an unparseable payload. */
function parseFrame(frame: string): RunEvent | null {
  const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
  if (!dataLine) return null
  try {
    const parsed = JSON.parse(dataLine.slice(5).trim()) as RunEvent
    return typeof parsed?.index === 'number' ? parsed : null
  } catch {
    return null
  }
}

/**
 * Start, watch and stop one chat run at a time.
 *
 * `onFinished` fires once per run when it reaches a terminal status, so the
 * caller can refetch the thread. The state here is the *live* view; the
 * conversation store remains the record, and the transcript is read from it
 * rather than assembled out of these events.
 */
export function useChatRun(onFinished?: () => void) {
  const [state, setState] = useState<RunState>(IDLE)
  const abort = useRef<AbortController | null>(null)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const watch = useCallback(async (runId: string, cursor = 0) => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    try {
      const response = await fetch(`${API}/chat/runs/${runId}/events?cursor=${cursor}`, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      })
      if (!response.body) throw new Error('the run stream returned no body')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let terminal = false
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // Frames are separated by a blank line. Anything after the last one is
        // a partial frame and stays in the buffer.
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''
        for (const frame of frames) {
          const event = parseFrame(frame)
          if (!event) continue
          setState((was) => apply(was, event))
          if (['completed', 'cancelled', 'failed'].includes(event.event)) terminal = true
        }
      }
      if (terminal) finished.current?.()
    } catch (error) {
      if ((error as Error).name === 'AbortError') return
      setState((was) => ({ ...was, status: 'failed', error: (error as Error).message }))
      finished.current?.()
    }
  }, [])

  const start = useCallback(
    async (conversationId: string, message: string) => {
      setState({ ...IDLE, status: 'accepted' })
      const run = await postJson<{ run_id: string }>(
        `/conversations/${conversationId}/runs`,
        { message },
      )
      setState((was) => ({ ...was, runId: run.run_id }))
      void watch(run.run_id)
      return run.run_id
    },
    [watch],
  )

  const stop = useCallback(async () => {
    const runId = state.runId
    if (!runId) return
    // Optimistic, and honest about what it means: the request cannot be
    // withdrawn from the provider, so this says "stopping" rather than
    // "stopped" until the run says which it was.
    setState((was) => ({ ...was, stage: 'Stopping…' }))
    await postJson(`/chat/runs/${runId}/cancel`, {})
  }, [state.runId])

  const reset = useCallback(() => {
    abort.current?.abort()
    setState(IDLE)
  }, [])

  /** Reattach to a run that was in flight when this client last looked. */
  const reattach = useCallback(
    (runId: string) => {
      setState({ ...IDLE, runId, status: 'running' })
      void watch(runId, 0)
    },
    [watch],
  )

  useEffect(() => () => abort.current?.abort(), [])

  return { state, start, stop, reset, reattach }
}
