import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ChatView } from './Chat'

/* Chat, driven through the interface a person uses.
 *
 * The properties asserted here are the ones the blocking request could not
 * have. `POST /conversations/{id}/messages` wrote the question, called the
 * model, waited, and returned the whole answer — so the question did not appear
 * until the answer did, there was nothing to stop, and a reload lost all
 * knowledge that a turn was in flight.
 *
 * The run stream is served through `fetch` with a `ReadableStream` body, which
 * is what the real client reads, so the fixture below emits real SSE frames
 * rather than a shape invented for the test.
 */

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))

const CONVERSATION = {
  conversation_id: 'c1',
  title: 'Opening range',
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
  archived: false,
  turn_count: 1,
  last_message: 'what has been validated?',
  context: [],
}

const TURN = {
  turn_id: 't1',
  conversation_id: 'c1',
  role: 'assistant',
  text: '## Findings\n\nNothing has cleared a holdout.\n\n| Strategy | Tier |\n| --- | --- |\n| orb_1 | VALIDATION_OOS |',
  created_at: '2026-09-01T10:00:05Z',
  provenance: 'model_prose',
  model: 'deepseek-v4-pro',
  tool_calls: [
    { name: 'list_strategies', arguments: {}, outcome: 'ok', reason: '', duration_ms: 12 },
  ],
  artifacts: [],
}

/** One SSE frame, exactly as `forge_api.control.run_events` writes it. */
const frame = (event: string, data: Record<string, unknown>, index: number) =>
  `event: ${event}\ndata: ${JSON.stringify({ index, event, data })}\n\n`

let runEvents: string[] = []
let latestRun: unknown = null
let turns: unknown[] = []
let started: string[] = []
let cancelled: string[] = []

function streamOf(frames: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const text of frames) controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

beforeEach(() => {
  runEvents = [
    frame('accepted', { run_id: 'run_1', conversation_id: 'c1' }, 0),
    frame('status', { stage: 'reading', detail: 'Reading this instance…' }, 1),
    frame('delta', { text: TURN.text }, 2),
    frame('completed', { run_id: 'run_1', status: 'completed', turn: TURN, elapsed_ms: 900 }, 3),
  ]
  latestRun = null
  turns = []
  started = []
  cancelled = []
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/chat/runs/') && url.includes('/events')) return streamOf(runEvents)
    if (url.includes('/chat/runs/') && url.endsWith('/cancel')) {
      cancelled.push(url)
      return new Response(JSON.stringify({ data: { run_id: 'run_1', cancelling: true } }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/runs') && init?.method === 'POST') {
      started.push(String(init.body))
      return new Response(JSON.stringify({ data: { run_id: 'run_1', status: 'accepted' } }), {
        status: 202, headers: { 'Content-Type': 'application/json' },
      })
    }
    const data = url.includes('/runs/latest') ? latestRun
      : url.includes('/conversations/c1') ? { conversation: CONVERSATION, turns }
      : url.includes('/conversations') ? [CONVERSATION]
      : url.endsWith('/strategies') ? []
      : url.endsWith('/campaigns') ? []
      : url.includes('/workstation') ? { context: { instrument: '', strategy: '', account: '' } }
      : {}
    return new Response(JSON.stringify({ data, meta: {} }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
})

afterEach(cleanup)

const draw = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ChatView /></QueryClientProvider>)
}

const ask = async (text: string) => {
  const box = await screen.findByLabelText(/ask a question/i)
  fireEvent.change(box, { target: { value: text } })
  fireEvent.submit(box.closest('form') as HTMLFormElement)
}

// ── the conversation ─────────────────────────────────────────────────────────

test('it opens as a conversation, not as a dashboard', async () => {
  draw()
  expect(await screen.findByRole('heading', { name: /ask algoforge/i })).toBeInTheDocument()
  expect(screen.getByLabelText(/ask a question/i)).toBeInTheDocument()
  // Two or three concrete prompts, not a wall of statistics.
  const suggestions = screen.getAllByRole('button').filter((b) => b.textContent?.endsWith('?'))
  expect(suggestions.length).toBeGreaterThanOrEqual(2)
  expect(suggestions.length).toBeLessThanOrEqual(4)
  expect(screen.queryByText(/orchestrator/i)).not.toBeInTheDocument()
})

test('the question appears before the answer does', async () => {
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('what has been validated?')
  // Optimistic: on screen before the network has answered anything. This is the
  // whole reason the run protocol exists — the blocking route could not show a
  // question until it had the reply.
  expect(await screen.findByText('what has been validated?')).toBeInTheDocument()
})

test('the reply arrives through the run stream and renders as markdown', async () => {
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('what has been validated?')
  expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
  expect(await screen.findByRole('table')).toBeInTheDocument()
})

test('a run is started against the open conversation', async () => {
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('anything')
  await waitFor(() => expect(started.length).toBe(1))
  expect(JSON.parse(started[0])).toEqual({ message: 'anything' })
})

// ── stopping ─────────────────────────────────────────────────────────────────

test('Send becomes Stop while a run is in flight, and Stop cancels it', async () => {
  // A stream that opens and stays open, so the run is observably running.
  runEvents = [frame('accepted', { run_id: 'run_1' }, 0), frame('status', { detail: 'Working…' }, 1)]
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('take your time')
  const stop = await screen.findByRole('button', { name: /stop/i })
  fireEvent.click(stop)
  await waitFor(() => expect(cancelled.length).toBe(1))
})

test('a cancelled run says what stopping actually did', async () => {
  runEvents = [
    frame('accepted', { run_id: 'run_1' }, 0),
    frame('cancelled', {
      run_id: 'run_1', status: 'cancelled',
      reason: 'stopped after the provider had already replied; the turn was kept',
    }, 1),
  ]
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('stop me')
  expect(await screen.findByText(/the turn was kept/i)).toBeInTheDocument()
})

// ── failure and retry ────────────────────────────────────────────────────────

test('a failed turn offers a retry rather than leaving a dead thread', async () => {
  runEvents = [
    frame('accepted', { run_id: 'run_1' }, 0),
    frame('failed', { run_id: 'run_1', status: 'failed', reason: 'the provider fell over' }, 1),
  ]
  draw()
  await screen.findByRole('heading', { name: /ask algoforge/i })
  await ask('break it')
  expect(await screen.findByText(/the provider fell over/i)).toBeInTheDocument()
  const retry = screen.getByRole('button', { name: /try again/i })
  fireEvent.click(retry)
  await waitFor(() => expect(started.length).toBe(2))
  expect(JSON.parse(started[1])).toEqual({ message: 'break it' })
})

// ── reattaching ──────────────────────────────────────────────────────────────

test('a reload reattaches to a run that was still going', async () => {
  latestRun = { run_id: 'run_1', status: 'running' }
  turns = [{ ...TURN, turn_id: 't0', role: 'user', text: 'asked before the reload', tool_calls: [], artifacts: [] }]
  draw()
  // The turn survives either way — it is written before the model is called —
  // but "the answer is still coming" is a fact only the run knows.
  expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
})

// ── what it does not show ────────────────────────────────────────────────────

test('routine tool calls are behind a disclosure, and a refusal is not', async () => {
  turns = [TURN]
  draw()
  const routine = await screen.findByText(/^1 step$/)
  expect(routine.closest('details')).not.toHaveAttribute('open')

  cleanup()
  turns = [{
    ...TURN,
    tool_calls: [{
      name: 'submit_orders', arguments: {}, outcome: 'refused',
      reason: "'submit_orders' reaches the book; only the autonomous stance runs it",
      duration_ms: 3,
    }],
  }]
  draw()
  // A refusal is the permission boundary working. Hiding it behind the same
  // disclosure as a routine call teaches the reader to scroll past both.
  const refused = await screen.findByText(/1 refused/)
  expect(refused.closest('details')).toHaveAttribute('open')
})

test('the standing of an answer is on every assistant turn', async () => {
  turns = [TURN]
  draw()
  // A user's stated belief, a model's paraphrase and a judged result look
  // identical rendered as chat. The mark is the only thing separating a
  // research transcript from a plausible one.
  const message = (await screen.findByText(/nothing has cleared a holdout/i)).closest('article')
  expect(within(message as HTMLElement).getByTitle(/./)).toBeInTheDocument()
})
