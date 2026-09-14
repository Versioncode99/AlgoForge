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
const sent: { path: string; message: string }[] = []
const attached: { path: string; body: unknown }[] = []
let workspaceContext = { instrument: '', timeframe: '', dataset: '', campaign: '', strategy: '', account: '' }
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
    // The workstation context is read through the action registry, the same
    // way every other surface reads it.
    if (path === '/actions/describe_context') {
      return { context: workspaceContext, groups: {}, panel_groups: [], panels: [] }
    }
    if (path.endsWith('/messages')) {
      if (sendFails) throw new Error('the model provider timed out')
      sent.push({ path, message: (body as { message: string }).message })
      return { turn: turn({ text: 'Understood.' }), conversation: conversation(), note: null }
    }
    if (path.endsWith('/context')) {
      attached.push({ path, body })
      return { ...(body as object), attached_at: 'now' }
    }
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
  attached.length = 0
  sendFails = false
  thread = null
  workspaceContext = {
    instrument: '',
    timeframe: '',
    dataset: '',
    campaign: '',
    strategy: '',
    account: '',
  }
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

  it('says so when an artifact names nothing a surface can open', async () => {
    conversations.push(conversation({ turn_count: 1 }))
    thread = {
      conversation: conversation(),
      turns: [
        turn({
          artifacts: [
            {
              artifact_id: 'a2',
              // Every *kind* resolves now: `port`, `parameter_surface` and
              // `resample` were each the example here in turn, and each one
              // stopped being it when its surface was built. What is left is
              // the case a kind cannot fix -- an artifact whose references do
              // not name the thing its destination needs. A strategy card with
              // no strategy in it has nowhere honest to point.
              kind: 'strategy',
              title: 'Strategy · from a blueprint',
              refs: { blueprint_id: 'bp1' },
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

  it('opens every artifact kind that names its subject', async () => {
    /* The counterpart to the assertion above, and the one that would catch a
       new kind arriving with no destination: each kind here is given the
       reference its destination needs, and every one of them must produce a
       link rather than the honest-label fallback. */
    conversations.push(conversation({ turn_count: 1 }))
    const kinds: { kind: string; refs: Record<string, string> }[] = [
      { kind: 'strategy', refs: { strategy_id: 's1' } },
      { kind: 'backtest', refs: { strategy_id: 's1' } },
      { kind: 'regime', refs: { strategy_id: 's1' } },
      { kind: 'resample', refs: { strategy_id: 's1' } },
      { kind: 'parameter_surface', refs: { strategy_id: 's1' } },
      { kind: 'validation', refs: { strategy_id: 's1' } },
      { kind: 'evidence', refs: { strategy_id: 's1' } },
      { kind: 'port', refs: { strategy_id: 's1' } },
      { kind: 'prop_simulation', refs: { account_id: 'a1' } },
      { kind: 'analysis', refs: { strategy_id: 's1' } },
      { kind: 'workspace', refs: { name: 'Desk' } },
    ]
    thread = {
      conversation: conversation(),
      turns: [
        turn({
          artifacts: kinds.map((item, index) => ({
            artifact_id: `k${index}`,
            kind: item.kind as never,
            title: `${item.kind} artifact`,
            refs: item.refs,
            provenance: 'deterministic' as const,
          })),
        }),
      ],
    }
    mount()
    const links = await screen.findAllByText('Open')
    expect(links).toHaveLength(kinds.length)
    expect(screen.queryByText('No panel yet')).not.toBeInTheDocument()
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
    await waitFor(() => expect(sent.map((s) => s.message)).toEqual(['What families can you build?']))
  })

  it('sends the first message to the conversation it just created', async () => {
    /* The bug this exists for: the send mutation used to close over the
     * component's `activeId`, and the id set by `create` is not readable until
     * React re-renders. So the first message of every new thread went to
     * `/conversations/null/messages`, 404'd, and was simply gone.
     *
     * Invisible to a mock that accepts any path ending in `/messages`, which
     * is what the original test did. The path is asserted now.
     */
    thread = { conversation: conversation(), turns: [] }
    mount()
    fireEvent.change(await screen.findByLabelText('Ask a question'), {
      target: { value: 'first ever question' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].path).toBe('/conversations/c-new/messages')
    expect(sent[0].path).not.toContain('null')
    expect(sent[0].path).not.toContain('undefined')
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

// ── what a new thread starts knowing ─────────────────────────────────────────

/** The workspace context arrives from its own request. Clicking before it
 *  lands is a real state -- the component handles it by reading the freshest
 *  value at mutation time -- but it is not the state these cases are about. */
async function contextLoaded() {
  const { postJson } = await import('../api')
  await waitFor(() =>
    expect(vi.mocked(postJson).mock.calls.some(([path]) => path === '/actions/describe_context')).toBe(
      true,
    ),
  )
}

describe('opening context', () => {
  it('inherits what the workspace is pointed at', async () => {
    /* The workstation already tracks the instrument, strategy and account the
     * operator is looking at. A conversation opened there should start knowing
     * the same things rather than making somebody type them again. */
    workspaceContext = { ...workspaceContext, instrument: 'NQ', strategy: 's1' }
    thread = { conversation: conversation(), turns: [] }
    mount()
    await contextLoaded()
    fireEvent.click((await screen.findAllByRole('button', { name: /New/ }))[0])
    await waitFor(() => expect(attached).toHaveLength(2))
    expect(attached.map((a) => (a.body as { kind: string }).kind).sort()).toEqual([
      'dataset',
      'strategy',
    ])
  })

  it('attaches to the conversation it just created', async () => {
    /* Same stale-closure trap as the first message: `activeId` is a render
     * behind, so the id has to come from the create call. */
    workspaceContext = { ...workspaceContext, instrument: 'NQ' }
    thread = { conversation: conversation(), turns: [] }
    mount()
    await contextLoaded()
    fireEvent.click((await screen.findAllByRole('button', { name: /New/ }))[0])
    await waitFor(() => expect(attached).toHaveLength(1))
    expect(attached[0].path).toBe('/conversations/c-new/context')
    expect(attached[0].path).not.toContain('null')
  })

  it('attaches nothing when the workspace is pointed at nothing', async () => {
    // An empty instrument would be a chip on screen naming nothing.
    thread = { conversation: conversation(), turns: [] }
    mount()
    fireEvent.click((await screen.findAllByRole('button', { name: /New/ }))[0])
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(attached).toEqual([])
  })

  it('prefers the context the panel was opened against', async () => {
    workspaceContext = { ...workspaceContext, instrument: 'NQ', account: 'acct-1' }
    thread = { conversation: conversation(), turns: [] }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ChatPanel opening={{ kind: 'strategy', ref: 's_explicit', label: 'NQ ORB' }} />
      </QueryClientProvider>,
    )
    fireEvent.click((await screen.findAllByRole('button', { name: /New/ }))[0])
    await waitFor(() => expect(attached).toHaveLength(1))
    expect((attached[0].body as { ref: string }).ref).toBe('s_explicit')
  })
})
