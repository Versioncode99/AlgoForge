import { ArrowUp, Check, MessageSquarePlus, Paperclip, RotateCcw, Search, Square, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { getJson } from '../api'
import { ArtifactVisual } from '../components/ArtifactVisual'
import { Markdown } from '../components/Markdown'
import {
  STANDING,
  type Artifact,
  type AttachedContext,
  type ContextKind,
  type Conversation,
  type ToolCall,
  type Turn,
  useArchiveConversation,
  useAttachContext,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useDetachContext,
  useThread,
} from '../chat'
import { useChatRun } from '../chat-run'
import type { ChatOpening } from '../components/ChatPanel'
import { format } from '../navigation'
import { useWorkstationContext } from '../workstation'

/* Chat: a conversation, at full width, with the work it produced beside it.
 *
 * **What this is not.** It is not "AI mode", it is not a console, and it is not
 * an operations view of an agent. Those are the three things the screen it
 * replaces was, and all three told the reader about the machinery rather than
 * about their question. The rail entry says Chat; the transcript says AlgoForge;
 * and what the assistant did shows up as a line of finished work, not as a plan,
 * a schema, or an agent's name.
 *
 * **What it keeps.** Everything the panel version had that was load-bearing:
 * persistent threads, search, archive and delete, attached context as visible
 * chips, artifacts that reopen the surface that computed them, and the standing
 * label on every turn. Standing in particular stays prominent — a user's stated
 * belief, a model's paraphrase and a judged result look identical rendered as
 * chat, and the mark is the only thing separating a research transcript from a
 * plausible one.
 *
 * **What is new.** The question appears the instant it is sent, the answer
 * arrives through a run that can be watched and stopped, a failed turn can be
 * retried, and the reply renders as Markdown — headings, lists, tables, fenced
 * code with a copy control — through a parser that cannot emit HTML.
 *
 * Deliberately absent: bubbles, avatars, typing dots, gradients, and any
 * animation that exists to suggest thinking. A message is a paragraph with a
 * label on it.
 */

const OPENERS = [
  'Do opening-range breakouts have predictive value on NQ?',
  'What has been validated out of sample, and on which window?',
  'Why did the last campaign stop?',
]

const CONTEXT_LABEL: Record<ContextKind, string> = {
  strategy: 'Strategy',
  experiment: 'Experiment',
  backtest: 'Backtest',
  dataset: 'Dataset',
  account: 'Account',
  workspace: 'Workspace',
  chart: 'Chart',
  finding: 'Finding',
  campaign: 'Campaign',
}

/** Where an artifact reopens, on the navigation this product has now. */
function destination(artifact: Artifact): string {
  const strategy = artifact.refs.strategy_id ?? ''
  const account = artifact.refs.account_id ?? ''
  const campaign = artifact.refs.campaign_id ?? ''
  switch (artifact.kind) {
    case 'regime':
    case 'backtest':
      return strategy ? format('strategies', 'trades', { strategy }) : ''
    case 'strategy':
    case 'evidence':
    case 'parameter_surface':
      return strategy ? format('strategies', 'library', { strategy }) : ''
    case 'validation':
      return strategy ? format('strategies', 'library', { strategy, pane: 'gates' }) : ''
    case 'port':
      return strategy ? format('strategies', 'library', { strategy, pane: 'port' }) : ''
    case 'resample':
      return strategy ? format('strategies', 'library', { strategy, pane: 'resample' }) : ''
    case 'prop_simulation':
      return format('propdesk', 'accounts', { account })
    case 'workspace':
      return format('home', 'workspace')
    case 'analysis':
      return format('research', 'workbench')
    case 'campaign':
      return format('campaigns', 'all', { campaign })
    default:
      return ''
  }
}

function Standing({ provenance }: { provenance: Turn['provenance'] }) {
  const standing = STANDING[provenance]
  return (
    <span className={`chat-standing ${provenance}`} title={standing.detail}>{standing.label}</span>
  )
}

/** What the assistant did, behind a disclosure. Never its reasoning.
 *
 * Routine tool calls were on screen for every turn, which taught the reader to
 * scroll past a region that sometimes contains a refusal. A refusal and a
 * failure are opposite facts — the boundary working, against something broken —
 * so a turn carrying either says so on the summary line rather than hiding it
 * behind the disclosure with everything else.
 */
function Activity({ calls }: { calls: ToolCall[] }) {
  if (!calls.length) return null
  const refused = calls.filter((call) => call.outcome === 'refused').length
  const failed = calls.filter((call) => call.outcome === 'failed').length
  const notable = refused + failed > 0
  const summary = notable
    ? [refused && `${refused} refused`, failed && `${failed} failed`].filter(Boolean).join(', ')
    : `${calls.length} step${calls.length === 1 ? '' : 's'}`
  return (
    <details className="chat-activity" open={notable}>
      <summary data-notable={notable || undefined}>{summary}</summary>
      <ul>
        {calls.map((call, index) => (
          <li key={`${call.name}-${index}`} className={call.outcome}>
            <span className="ca-mark" aria-hidden="true">
              {call.outcome === 'ok' ? '✓' : call.outcome === 'refused' ? '⦸' : '✕'}
            </span>
            <code>{call.name}</code>
            {call.outcome !== 'ok' && <span className="ca-word">{call.outcome}</span>}
            {call.duration_ms > 0 && <span className="ca-ms mono">{call.duration_ms}ms</span>}
            {call.reason && <p className="ca-reason">{call.reason}</p>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function Artifacts({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null
  return (
    <div className="chat-artifacts">
      {artifacts.map((artifact) => {
        const href = destination(artifact)
        const refs = Object.entries(artifact.refs).map(([k, v]) => `${k}=${v}`).join(' · ')
        return (
          <div key={artifact.artifact_id} className="chat-artifact">
            <span className="art-kind">{artifact.kind.replace(/_/g, ' ')}</span>
            <strong>{artifact.title}</strong>
            {/* References, not figures. An artifact is a way back to what the
                deterministic system says now, never a copy of what it said
                then. */}
            <span className="art-refs mono">{refs}</span>
            <ArtifactVisual artifact={artifact} />
            {href
              ? <a className="art-open" href={href}>Open</a>
              : <span className="art-open disabled">No surface for this yet</span>}
          </div>
        )
      })}
    </div>
  )
}

function Message({ turn }: { turn: Turn }) {
  const mine = turn.role === 'user'
  return (
    <article className={`chat-msg ${turn.role}`}>
      <header>
        <span className="chat-who">{mine ? 'You' : 'AlgoForge'}</span>
        {!mine && <Standing provenance={turn.provenance} />}
        {turn.model && <span className="chat-model mono">{turn.model}</span>}
      </header>
      {mine ? <p className="chat-text">{turn.text}</p> : <Markdown text={turn.text} />}
      <Activity calls={turn.tool_calls} />
      <Artifacts artifacts={turn.artifacts} />
    </article>
  )
}

export function ChatView({ opening }: { opening?: ChatOpening } = {}) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(true)
  const [attaching, setAttaching] = useState(false)

  const workspace = useWorkstationContext()
  const conversations = useConversations(query)
  const thread = useThread(activeId)
  const create = useCreateConversation()
  const archive = useArchiveConversation()
  const remove = useDeleteConversation()
  const attach = useAttachContext()
  const detach = useDetachContext()

  const run = useChatRun((status) => {
    // The question stays on screen unless it was answered. A failed or stopped
    // run leaves it unanswered, and clearing it would take away both the record
    // of what was asked and the thing Try again retries.
    if (status === 'completed') setPending(null)
    void thread.refetch()
    void conversations.refetch()
  })

  const rows = useMemo(() => conversations.data ?? [], [conversations.data])

  // Open the most recent thread on arrival. Opening on "start a new chat" with
  // twenty saved threads hides the history that is the point of storing them.
  useEffect(() => {
    if (!activeId && rows.length) setActiveId(rows[0].conversation_id)
  }, [activeId, rows])

  /* Reattach to a run that was in flight when this client last looked.
   *
   * The turn survives a reload either way — it is written before the model is
   * called — but "the answer is still coming" is a fact only the run knows, and
   * without this a reloaded window shows a question with no answer and no
   * indication that one is on its way. */
  useEffect(() => {
    if (!activeId) return
    let cancelled = false
    void getJson<{ run_id: string; status: string } | null>(
      `/conversations/${activeId}/runs/latest`,
    ).then((latest) => {
      if (cancelled || !latest) return
      if (latest.status === 'accepted' || latest.status === 'running') run.reattach(latest.run_id)
    }).catch(() => undefined)
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

  const bottom = useRef<HTMLDivElement | null>(null)
  const thread_ = thread.data
  useEffect(() => {
    const anchor = bottom.current
    // Guarded rather than called: `scrollIntoView` is optional in the DOM, and
    // a panel that throws where the host does not implement it has made a
    // convenience into a crash.
    if (anchor && typeof anchor.scrollIntoView === 'function') {
      anchor.scrollIntoView({ block: 'end', behavior: 'auto' })
    }
  }, [thread_?.turns.length, pending, run.state.text])

  const inherited = opening?.ref
    ? [{ kind: opening.kind, ref: opening.ref, label: opening.label ?? opening.ref }]
    : ([['dataset', workspace.data?.context?.instrument],
        ['strategy', workspace.data?.context?.strategy],
        ['account', workspace.data?.context?.account]] as [ContextKind, string | undefined][])
        .filter(([, ref]) => Boolean(ref))
        .map(([kind, ref]) => ({ kind, ref: ref as string, label: ref as string }))
  const latestContext = useRef(inherited)
  latestContext.current = inherited

  const startThread = (then?: (id: string) => void) => {
    create.mutate('', {
      onSuccess: (conversation) => {
        setActiveId(conversation.conversation_id)
        for (const item of latestContext.current) {
          // The id from the create call: `activeId` is a render behind here.
          attach.mutate({ conversationId: conversation.conversation_id, ...item })
        }
        then?.(conversation.conversation_id)
      },
    })
  }

  const submit = (text: string) => {
    const message = text.trim()
    if (!message || busy) return
    setDraft('')
    // The question is on screen before the network is touched. This is the
    // whole reason the run protocol exists: it used to appear only when the
    // answer did, so a nine-second model looked like a broken Send button.
    setPending(message)
    if (!activeId) {
      startThread((id) => void run.start(id, message))
      return
    }
    void run.start(activeId, message)
  }

  const busy = run.state.status === 'accepted' || run.state.status === 'running'
  const turns = thread.data?.turns ?? []
  const context = thread.data?.conversation.context ?? []
  const failed = run.state.status === 'failed'
  const lastQuestion = pending ?? [...turns].reverse().find((t) => t.role === 'user')?.text ?? ''
  /* The finished answer, until the thread refetch brings back the real turn.
   *
   * Without this the reply *disappears* the instant the run completes and
   * reappears when the refetch lands — a flash on every single turn, and on a
   * slow read a blank transcript where an answer just was. The run's text and
   * the stored turn are the same words; this is which of the two is on screen. */
  const settled = run.state.text
    && !busy
    && !turns.some((t) => t.role === 'assistant' && t.text === run.state.text)

  return (
    <div className="chat-view">
      <aside className="chat-history" data-open={historyOpen}>
        <div className="ch-head">
          <button type="button" className="btn tiny primary" onClick={() => {
            run.reset()
            setPending(null)
            startThread()
          }}>
            <MessageSquarePlus aria-hidden="true" /> New chat
          </button>
          <button
            type="button"
            className="ch-collapse"
            aria-expanded={historyOpen}
            aria-label={historyOpen ? 'Hide conversations' : 'Show conversations'}
            onClick={() => setHistoryOpen((was) => !was)}
          >{historyOpen ? '‹' : '›'}</button>
        </div>
        <label className="ch-search">
          <Search aria-hidden="true" />
          <input
            aria-label="Search conversations"
            placeholder="Search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <ul>
          {rows.map((item: Conversation) => (
            <li key={item.conversation_id} className={item.conversation_id === activeId ? 'on' : ''}>
              <button type="button" className="ch-open" onClick={() => {
                run.reset()
                setPending(null)
                setActiveId(item.conversation_id)
              }}>
                <strong>{item.title}</strong>
                <span className="ch-preview">{item.last_message}</span>
              </button>
              <span className="ch-tools">
                <button type="button" aria-label={`Archive ${item.title}`}
                  onClick={() => archive.mutate({ id: item.conversation_id, archived: true })}>
                  <Check aria-hidden="true" />
                </button>
                <button type="button" aria-label={`Delete ${item.title}`}
                  onClick={() => {
                    remove.mutate(item.conversation_id)
                    if (item.conversation_id === activeId) setActiveId(null)
                  }}>
                  <Trash2 aria-hidden="true" />
                </button>
              </span>
            </li>
          ))}
          {rows.length === 0 && (
            <li className="ch-none">{query ? 'Nothing matches that.' : 'No conversations yet.'}</li>
          )}
        </ul>
      </aside>

      <section className="chat-main">
        <div className="chat-thread" role="log" aria-label="Conversation" aria-busy={busy}>
          {turns.length === 0 && !pending && (
            <div className="chat-empty">
              <h2>Ask AlgoForge</h2>
              <p>
                Answers come from this instance’s own record — its strategies, backtests, verdicts
                and campaigns — through the same bounded actions the rest of the product uses. It
                will not predict markets or suggest trades, and every reply says how it was made.
              </p>
              <div className="chat-suggest">
                {OPENERS.map((opener) => (
                  <button key={opener} type="button" onClick={() => submit(opener)}>{opener}</button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn) => <Message key={turn.turn_id} turn={turn} />)}

          {/* The optimistic question. Dropped the moment the thread refetch
              brings back the real turn, so it never renders twice. */}
          {pending && !turns.some((t) => t.role === 'user' && t.text === pending) && (
            <article className="chat-msg user is-pending">
              <header><span className="chat-who">You</span></header>
              <p className="chat-text">{pending}</p>
            </article>
          )}

          {busy && (
            <article className="chat-msg assistant is-running" aria-live="polite">
              <header><span className="chat-who">AlgoForge</span></header>
              {run.state.text
                ? <Markdown text={run.state.text} />
                : <p className="chat-working">{run.state.stage || 'Working…'}</p>}
            </article>
          )}

          {settled && (
            <article className="chat-msg assistant">
              <header><span className="chat-who">AlgoForge</span></header>
              <Markdown text={run.state.text} />
            </article>
          )}

          {failed && (
            <div className="chat-failed" role="alert">
              <p>{run.state.error}</p>
              <button type="button" className="btn tiny" onClick={() => submit(lastQuestion)}>
                <RotateCcw aria-hidden="true" /> Try again
              </button>
            </div>
          )}
          {run.state.status === 'cancelled' && (
            <p className="chat-stopped" role="status">Stopped. {run.state.error}</p>
          )}

          <div ref={bottom} />
        </div>

        <form
          className="chat-composer"
          onSubmit={(event) => { event.preventDefault(); submit(draft) }}
        >
          {context.length > 0 && (
            <div className="chat-context" aria-label="Attached context">
              {context.map((item: AttachedContext) => (
                <span key={`${item.kind}:${item.ref}`} className="chat-chip">
                  <i>{CONTEXT_LABEL[item.kind]}</i>{item.label || item.ref}
                  <button type="button" aria-label={`Detach ${item.label || item.ref}`}
                    onClick={() => activeId && detach.mutate({
                      conversationId: activeId, kind: item.kind, ref: item.ref,
                    })}>×</button>
                </span>
              ))}
            </div>
          )}
          <div className="chat-composer-row">
            <button
              type="button"
              className="chat-attach"
              aria-expanded={attaching}
              aria-label="Attach context"
              title="Put a strategy, campaign or account in scope for this conversation"
              onClick={() => setAttaching((was) => !was)}
            ><Paperclip aria-hidden="true" /></button>
            <textarea
              aria-label="Ask a question"
              placeholder="Ask about strategies, campaigns, evidence or the engine…"
              rows={1}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends; Shift+Enter is a newline. The reverse would make
                // a multi-line question require a mouse.
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submit(draft)
                }
              }}
            />
            {busy ? (
              <button type="button" className="chat-send is-stop" onClick={() => void run.stop()}>
                <Square aria-hidden="true" /> Stop
              </button>
            ) : (
              <button className="chat-send" type="submit" disabled={!draft.trim()}>
                <ArrowUp aria-hidden="true" /><span className="sr-only">Send</span>
              </button>
            )}
          </div>
          {attaching && <AttachPicker
            onPick={(item) => {
              setAttaching(false)
              if (activeId) attach.mutate({ conversationId: activeId, ...item })
              else startThread((id) => attach.mutate({ conversationId: id, ...item }))
            }}
            onClose={() => setAttaching(false)}
          />}
        </form>
      </section>
    </div>
  )
}

/** Put something in scope, from what this instance actually holds.
 *
 * Only strategies and campaigns, and only by name. Attaching resolves to an
 * identifier the conversation records; the *content* is never copied into the
 * prompt, because a conversation whose context is everything it has ever named
 * is a conversation that costs more every turn.
 */
function AttachPicker({ onPick, onClose }: {
  onPick: (item: { kind: ContextKind; ref: string; label: string }) => void
  onClose: () => void
}) {
  const [strategies, setStrategies] = useState<{ strategy_id: string; name: string }[]>([])
  const [campaigns, setCampaigns] = useState<{ campaign_id: string; name: string }[]>([])
  useEffect(() => {
    void getJson<{ strategy_id: string; name: string }[]>('/strategies')
      .then(setStrategies).catch(() => undefined)
    void getJson<{ campaign_id: string; name: string }[]>('/campaigns')
      .then(setCampaigns).catch(() => undefined)
  }, [])
  return (
    <div className="chat-attach-menu" role="dialog" aria-label="Attach context">
      <div>
        <h3>Strategies</h3>
        <ul>
          {strategies.slice(0, 12).map((item) => (
            <li key={item.strategy_id}>
              <button type="button" onClick={() => onPick({
                kind: 'strategy', ref: item.strategy_id, label: item.name,
              })}>{item.name}</button>
            </li>
          ))}
          {!strategies.length && <li className="ch-none">None yet.</li>}
        </ul>
      </div>
      <div>
        <h3>Campaigns</h3>
        <ul>
          {campaigns.slice(0, 12).map((item) => (
            <li key={item.campaign_id}>
              <button type="button" onClick={() => onPick({
                kind: 'campaign', ref: item.campaign_id, label: item.name,
              })}>{item.name}</button>
            </li>
          ))}
          {!campaigns.length && <li className="ch-none">None yet.</li>}
        </ul>
      </div>
      <button type="button" className="btn tiny" onClick={onClose}>Close</button>
    </div>
  )
}
