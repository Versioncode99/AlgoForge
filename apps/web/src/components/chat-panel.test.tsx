import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Conversation, Thread, Turn } from '../chat'
import { ChatPanel } from './ChatPanel'

/* The conversation panel, and the one thing it must never make comfortable.
 *
 * A chat surface renders a stated belief, a model's paraphrase and a judged
 * result as three similar paragraphs. The backend tracks which is which so
 * this layer can mark it; a design that hid the mark to look calmer would
 * discard the only thing separating a research transcript from a plausible
 * one. Most of these tests are about that mark surviving.
 *
 * The rest are about the panel being a panel: history that is actually there
 * when you return, a question that is not lost when the answer fails, and a
 * refusal that does not read like a bug.
 */

const conversations: Conversation[] = []
let thread: Thread | null = null
const sent: string[] = []
let sendFails = false

function turn(over: Partial<Turn> = {}): Turn {
  return {
    turn_id: `t${Math.random()}`,
    conversation_id: 'c1',
    role: 'assistant',
    text: 'An answer.',
    created_at: '2026-09-13T12:00:00+00:00',
    provenance: 'deterministic',
    model: '',
    tool_calls: [],
    artifacts: [],
    ...over,
  }
}

function conversation(over: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: 'c1',
    title: 'NQ volatility',
    created_at: '2026-09-13T12:00:00+00:00',
    updated_at: '2026-09-13T12:00:00+00:00',
    archived: false,
    turn_count: 0,
    context: [],
    last_message: '',
    ...over,
  }
}

vi.mock('../api', () => ({
  getJson: vi.fn(async (path: string) => {
    if (path.startsWith('/conversations?') || path === '/conversations') return conversations
    if (path.startsWith('/conversations/')) return thread
    return null
  }),
  postJson: vi.fn(async (path: string, body: unknown) => {
    if (path.endsWith('/messages')) {
      if (sendFails) throw new Error('the model provider timed out')
      sent.push((body as { message: string }).message)
      return { turn: turn({ text: 'Understood.' }), conversation: conversation(), note: null }
    }
    if (path.endsWith('/context')) return { ...(body as object), attached_at: 'now' }
    if (path === '/conversations') {
      const created = conversation({ conversation_id: 'c-new', title: 'New conversation' })
      conversations.unshift(created)
      return created
    }
    return {}
  }),
  patchJson: vi.fn(async () => conversation()),
  deleteJson: vi.fn(async () => ({})),
}))

function mount(props: Parameters<typeof ChatPanel>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  conversations.length = 0
  sent.length = 0
  sendFails = false
  thread = null
})

afterEach(() => vi.clearAllMocks())

// ── standing ─────────────────────────────────────────────────────────────────

describe('standing', () => {
  it('marks a judged answer differently from a model’s sentence', async () => {
    /* The distinction the whole store exists to keep. Rendered identically,
     * the second is eventually read as the first. */
    conversations.push(conversation({ turn_count: 4 }))
    thread = {
      conversation: conversation({ turn_count: 4 }),
      turns: [
        turn({ role: 'user', text: 'Is the edge volatility dependent?', provenance: 'user_statement' }),
        turn({ text: 'It appears so.', provenance: 'model_prose', model: 'some-model' }),
        turn({ text: 'G6 failed on the high-volatility fold.', provenance: 'deterministic' }),
        turn({ text: '4 strategies.', provenance: 'action_result' }),
      ],
    }
    mount()
    expect(await screen.findByText('You said')).toBeInTheDocument()
    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Deterministic')).toBeInTheDocument()
    expect(screen.getByText('Action result')).toBeInTheDocument()
  })

  it('explains what each standing means where it is shown', async () => {
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation(),
      turns: [turn({ text: 'It appears so.', provenance: 'model_prose', model: 'm' })],
    }
    mount()
    const badge = await screen.findByText('Model')
    // A model's sentence informed by actions is still the model's sentence,
    // and the explanation has to say so rather than implying the figures make
    // the prose authoritative.
    expect(badge).toHaveAttribute('title', expect.stringContaining('still the model’s'))
  })

  it('names the model that answered, and stays silent when none did', async () => {
    conversations.push(conversation({ turn_count: 2 }))
    thread = {
      conversation: conversation(),
      turns: [
        turn({ text: 'From the ledger.', provenance: 'deterministic', model: '' }),
        turn({ text: 'From a model.', provenance: 'model_prose', model: 'claude-x' }),
      ],
    }
    mount()
    expect(await screen.findByText('claude-x')).toBeInTheDocument()
    // A deterministic answer reporting a model name would look like something
    // a model reasoned out.
    expect(screen.queryByText('local-ledger')).not.toBeInTheDocument()
  })
})

// ── what it did ──────────────────────────────────────────────────────────────

describe('tool activity', () => {
  it('shows a refusal as a refusal and a failure as a failure', async () => {
    /* Opposite facts: one is the permission boundary working, the other is
     * something broken. One grey "error" row would teach somebody to ignore
     * both. */
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation(),
      turns: [
        turn({
          text: 'I could not do all of that.',
          provenance: 'model_prose',
          model: 'm',
          tool_calls: [
            { name: 'list_strategies', arguments: {}, outcome: 'ok', reason: '', duration_ms: 4, result_ref: '' },
            {
              name: 'submit_orders',
              arguments: {},
              outcome: 'refused',
              reason: "'submit_orders' reaches the book; a person applies it",
              duration_ms: 1,
              result_ref: '',
            },
            { name: 'backtest_strategy', arguments: {}, outcome: 'failed', reason: 'worker timed out', duration_ms: 120_000, result_ref: '' },
          ],
        }),
      ],
    }
    mount()
    expect(await screen.findByText('refused')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    // The reason is carried verbatim: paraphrasing a refusal makes it
    // uncheckable against the rule that produced it.
    expect(screen.getByText(/reaches the book; a person applies it/)).toBeInTheDocument()
    expect(screen.getByText('worker timed out')).toBeInTheDocument()
  })

  it('shows no activity block when nothing ran', async () => {
    conversations.push(conversation({ turn_count: 1 }))
    thread = { conversation: conversation(), turns: [turn({ text: 'Just prose.' })] }
    const { container } = mount()
    await screen.findByText('Just prose.')
    expect(container.querySelector('.chat-activity')).toBeNull()
  })
})

// ── artifacts ────────────────────────────────────────────────────────────────

describe('artifacts', () => {
  it('shows what an artifact points at rather than what it found', async () => {
    /* References, never figures. A number written into a stored artifact is one
     * nobody computed, indistinguishable later from one that was. */
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation(),
      turns: [
        turn({
          artifacts: [
            {
              artifact_id: 'a1',
              kind: 'regime',
              title: 'Regime attribution · s1',
              refs: { strategy_id: 's1', backtest_id: 'b1' },
              provenance: 'deterministic',
            },
          ],
        }),
      ],
    }
    mount()
    const card = (await screen.findByText('Regime attribution · s1')).closest('.chat-artifact')!
    expect(within(card as HTMLElement).getByText(/strategy_id=s1/)).toBeInTheDocument()
    expect(within(card as HTMLElement).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      expect.stringContaining('s1'),
    )
  })

  it('says so when a kind has no panel to open yet', async () => {
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation(),
      turns: [
        turn({
          artifacts: [
            {
              artifact_id: 'a2',
              kind: 'port',
              title: 'Ported strategy · s1',
              refs: { strategy_id: 's1', target: 'pine' },
              provenance: 'deterministic',
            },
          ],
        }),
      ],
    }
    mount()
    // A button that goes nowhere is worse than an honest label.
    expect(await screen.findByText('No panel yet')).toBeInTheDocument()
  })
})

// ── the thread as a durable thing ────────────────────────────────────────────

describe('history', () => {
  it('opens on the most recent thread rather than an empty screen', async () => {
    /* A panel that greets somebody with "start a new chat" when they have
     * twenty saved threads has hidden the history that is the point of
     * storing them. */
    conversations.push(
      conversation({ conversation_id: 'c1', title: 'Most recent', turn_count: 2 }),
      conversation({ conversation_id: 'c2', title: 'Older' }),
    )
    thread = {
      conversation: conversation({ title: 'Most recent' }),
      turns: [turn({ text: 'Where we left off.' })],
    }
    mount()
    expect(await screen.findByText('Where we left off.')).toBeInTheDocument()
  })

  it('lists threads with enough to recognise them', async () => {
    conversations.push(
      conversation({ conversation_id: 'c1', title: 'NQ volatility', turn_count: 6, last_message: 'the 75th percentile' }),
    )
    thread = { conversation: conversation(), turns: [] }
    mount()
    expect(await screen.findByText('the 75th percentile')).toBeInTheDocument()
    expect(screen.getByText('6 turns')).toBeInTheDocument()
  })

  it('says nothing matched rather than looking empty', async () => {
    thread = null
    mount()
    await waitFor(() => expect(screen.getByText('No conversations yet.')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Search conversations'), {
      target: { value: 'absorption' },
    })
    await waitFor(() => expect(screen.getByText('Nothing matches that.')).toBeInTheDocument())
  })
})

// ── asking ───────────────────────────────────────────────────────────────────

describe('asking', () => {
  it('starts a conversation when there is none and still sends the question', async () => {
    thread = { conversation: conversation(), turns: [] }
    mount()
    const input = await screen.findByLabelText('Ask a question')
    fireEvent.change(input, { target: { value: 'What families can you build?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(sent).toEqual(['What families can you build?']))
  })

  it('reports a failed answer instead of leaving the panel silent', async () => {
    conversations.push(conversation({ turn_count: 0 }))
    thread = { conversation: conversation(), turns: [] }
    sendFails = true
    mount()
    const input = await screen.findByLabelText('Ask a question')
    fireEvent.change(input, { target: { value: 'anything' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('the model provider timed out')
  })

  it('refuses to send an empty message', async () => {
    conversations.push(conversation())
    thread = { conversation: conversation(), turns: [] }
    mount()
    expect(await screen.findByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('offers openings that are questions about this instance, not prompts about trading', async () => {
    conversations.push(conversation())
    thread = { conversation: conversation(), turns: [] }
    mount()
    expect(await screen.findByText(/How many strategies are there/)).toBeInTheDocument()
    expect(screen.getByText(/will not predict markets or suggest trades/)).toBeInTheDocument()
  })
})

// ── context ──────────────────────────────────────────────────────────────────

describe('attached context', () => {
  it('shows what the conversation is about', async () => {
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation({
        context: [
          { kind: 'strategy', ref: 's1', label: 'NQ ORB', attached_at: '2026-09-13T12:00:00+00:00' },
        ],
      }),
      turns: [turn()],
    }
    mount()
    // Visible rather than assembled behind the scenes: what the assistant can
    // see is something the operator should be able to read off the screen.
    expect(await screen.findByText('NQ ORB')).toBeInTheDocument()
    expect(screen.getByText('Strategy')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Detach NQ ORB' })).toBeInTheDocument()
  })

  it('shows no context row when nothing is attached', async () => {
    conversations.push(conversation())
    thread = { conversation: conversation(), turns: [turn()] }
    const { container } = mount()
    await screen.findByText('An answer.')
    expect(container.querySelector('.chat-context')).toBeNull()
  })
})
