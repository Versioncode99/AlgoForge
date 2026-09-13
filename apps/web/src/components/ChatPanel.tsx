import { useEffect, useMemo, useRef, useState } from 'react'
import { Archive, MessageSquarePlus, Search, Trash2, X } from 'lucide-react'
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
  useSendMessage,
  useThread,
} from '../chat'

/* A research conversation, as a panel rather than as a screen.
 *
 * Two decisions shape everything here.
 *
 * **It is a panel.** The chat does not get its own mode, its own route or its
 * own window. It goes in a workspace beside a chart and a strategy tester,
 * gets dragged and resized like anything else, and is saved with the layout —
 * because the point is not to visit the AI, it is to have it where the work is.
 * That is also why the whole thing works at panel width: the history list
 * collapses, the composer stays, and nothing here assumes a full screen.
 *
 * **Standing is shown, not smoothed over.** A user's stated belief, a model's
 * paraphrase and a judged result look identical rendered as chat. The backend
 * tracks which is which precisely so this layer can mark it, and a design that
 * hid the mark to look calmer would throw away the only thing separating a
 * research transcript from a plausible one.
 *
 * Deliberately absent: bubbles, avatars, typing dots, gradients, and any
 * animation that exists to suggest thinking. A message is a paragraph with a
 * label on it.
 */

const SUGGESTIONS = [
  'How many strategies are there and are any profitable?',
  'What families can you build?',
  'Why did the last candidates get rejected?',
  'What is the engine doing?',
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

/** Where an artifact reopens. Empty when the surface takes no deep link yet —
 *  in which case the artifact still lists its references, because a reference
 *  somebody can copy beats a button that goes nowhere. */
function destination(artifact: Artifact): string {
  const strategy = artifact.refs.strategy_id
  switch (artifact.kind) {
    case 'regime':
    case 'backtest':
      return strategy ? `#trades?strategy=${encodeURIComponent(strategy)}` : ''
    case 'strategy':
    case 'evidence':
    case 'validation':
      return strategy ? `#strategies?strategy=${encodeURIComponent(strategy)}` : ''
    case 'workspace':
      return '#workspace'
    case 'analysis':
      return '#lab'
    default:
      return ''
  }
}

function Standing({ provenance }: { provenance: Turn['provenance'] }) {
  const standing = STANDING[provenance]
  return (
    <span className={`chat-standing ${provenance}`} title={standing.detail}>
      {standing.label}
    </span>
  )
}

/** What the assistant did, summarised. Never its reasoning.
 *
 * A refusal is rendered differently from a failure because they mean opposite
 * things: one is the permission boundary working, the other is something
 * broken, and a single grey "error" row would teach somebody to ignore both.
 */
function ToolActivity({ calls }: { calls: ToolCall[] }) {
  if (!calls.length) return null
  return (
    <ul className="chat-activity">
      {calls.map((call, index) => (
        <li key={`${call.name}-${index}`} className={call.outcome}>
          <span className="ca-mark" aria-hidden="true">
            {call.outcome === 'ok' ? '✓' : call.outcome === 'refused' ? '⦸' : '✕'}
          </span>
          <code>{call.name}</code>
          {call.outcome === 'refused' && <span className="ca-word">refused</span>}
          {call.outcome === 'failed' && <span className="ca-word">failed</span>}
          {call.duration_ms > 0 && <span className="ca-ms mono">{call.duration_ms}ms</span>}
          {call.reason && <p className="ca-reason">{call.reason}</p>}
        </li>
      ))}
    </ul>
  )
}

function Artifacts({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null
  return (
    <div className="chat-artifacts">
      {artifacts.map((artifact) => {
        const href = destination(artifact)
        const refs = Object.entries(artifact.refs)
          .map(([key, value]) => `${key}=${value}`)
          .join(' · ')
        return (
          <div key={artifact.artifact_id} className="chat-artifact">
            <span className="art-kind">{artifact.kind.replace(/_/g, ' ')}</span>
            <strong>{artifact.title}</strong>
            {/* References, not figures. The artifact is a way back to whatever
                the deterministic system says now, never a copy of what it said
                then. */}
            <span className="art-refs mono">{refs}</span>
            {href ? (
              <a className="art-open" href={href}>
                Open
              </a>
            ) : (
              <span className="art-open disabled">No panel yet</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Message({ turn }: { turn: Turn }) {
  return (
    <article className={`chat-msg ${turn.role}`}>
      <header>
        <span className="chat-who">{turn.role === 'user' ? 'You' : 'AlgoForge'}</span>
        <Standing provenance={turn.provenance} />
        {turn.model && <span className="chat-model mono">{turn.model}</span>}
      </header>
      <p className="chat-text">{turn.text}</p>
      <ToolActivity calls={turn.tool_calls} />
      <Artifacts artifacts={turn.artifacts} />
    </article>
  )
}

function ContextChips({
  context,
  onDetach,
}: {
  context: AttachedContext[]
  onDetach: (item: AttachedContext) => void
}) {
  if (!context.length) return null
  return (
    <div className="chat-context" aria-label="Attached context">
      {context.map((item) => (
        <span key={`${item.kind}:${item.ref}`} className="chat-chip">
          <i>{CONTEXT_LABEL[item.kind]}</i>
          {item.label || item.ref}
          <button
            type="button"
            aria-label={`Detach ${item.label || item.ref}`}
            onClick={() => onDetach(item)}
          >
            <X aria-hidden="true" />
          </button>
        </span>
      ))}
    </div>
  )
}

function History({
  conversations,
  activeId,
  query,
  onQuery,
  onOpen,
  onNew,
  onArchive,
  onDelete,
}: {
  conversations: Conversation[]
  activeId: string | null
  query: string
  onQuery: (value: string) => void
  onOpen: (id: string) => void
  onNew: () => void
  onArchive: (id: string) => void
  onDelete: (id: string) => void
}) {
  return (
    <aside className="chat-history">
      <div className="ch-head">
        <button type="button" className="btn tiny primary" onClick={onNew}>
          <MessageSquarePlus aria-hidden="true" /> New
        </button>
        <label className="ch-search">
          <Search aria-hidden="true" />
          <input
            aria-label="Search conversations"
            placeholder="Search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
          />
        </label>
      </div>
      <ul>
        {conversations.map((item) => (
          <li key={item.conversation_id} className={item.conversation_id === activeId ? 'on' : ''}>
            <button type="button" className="ch-open" onClick={() => onOpen(item.conversation_id)}>
              <strong>{item.title}</strong>
              <span className="ch-preview">{item.last_message}</span>
              <span className="ch-meta mono">{item.turn_count} turns</span>
            </button>
            <span className="ch-tools">
              <button
                type="button"
                aria-label={`Archive ${item.title}`}
                onClick={() => onArchive(item.conversation_id)}
              >
                <Archive aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={`Delete ${item.title}`}
                onClick={() => onDelete(item.conversation_id)}
              >
                <Trash2 aria-hidden="true" />
              </button>
            </span>
          </li>
        ))}
        {conversations.length === 0 && (
          <li className="ch-none">
            {query ? 'Nothing matches that.' : 'No conversations yet.'}
          </li>
        )}
      </ul>
    </aside>
  )
}

export function ChatPanel({
  /** Context the panel was opened against — a strategy, a chart's instrument.
   *  Attached to a new conversation on creation so the thread starts knowing
   *  what it is about, rather than the operator having to say it again. */
  opening,
  compact = false,
}: {
  opening?: { kind: ContextKind; ref: string; label?: string }
  compact?: boolean
}) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState('')
  const [showHistory, setShowHistory] = useState(!compact)

  const conversations = useConversations(query)
  const thread = useThread(activeId)
  const create = useCreateConversation()
  const send = useSendMessage(activeId)
  const archive = useArchiveConversation()
  const remove = useDeleteConversation()
  const attach = useAttachContext(activeId)
  const detach = useDetachContext(activeId)

  const rows = useMemo(() => conversations.data ?? [], [conversations.data])

  // Open the most recent thread on arrival. A panel that opens on an empty
  // "start a new chat" screen when there are twenty saved threads has hidden
  // the history that is the point of storing them.
  useEffect(() => {
    if (!activeId && rows.length) setActiveId(rows[0].conversation_id)
  }, [activeId, rows])

  const bottom = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // Guarded rather than called: `scrollIntoView` is optional in the DOM, and
    // a conversation panel that throws where the host does not implement it
    // has made a convenience into a crash.
    const anchor = bottom.current
    if (anchor && typeof anchor.scrollIntoView === 'function') {
      anchor.scrollIntoView({ block: 'end' })
    }
  }, [thread.data?.turns.length, send.isPending])

  const start = () => {
    create.mutate('', {
      onSuccess: (conversation) => {
        setActiveId(conversation.conversation_id)
        if (opening?.ref) {
          attach.mutate({
            kind: opening.kind,
            ref: opening.ref,
            label: opening.label ?? opening.ref,
          })
        }
      },
    })
  }

  const submit = (text: string) => {
    const message = text.trim()
    if (!message) return
    if (!activeId) {
      create.mutate('', {
        onSuccess: (conversation) => {
          setActiveId(conversation.conversation_id)
          send.mutate(message)
          setDraft('')
        },
      })
      return
    }
    send.mutate(message)
    setDraft('')
  }

  const turns = thread.data?.turns ?? []
  const context = thread.data?.conversation.context ?? []

  return (
    <div className={compact ? 'chat-panel compact' : 'chat-panel'}>
      {showHistory && (
        <History
          conversations={rows}
          activeId={activeId}
          query={query}
          onQuery={setQuery}
          onOpen={(id) => {
            setActiveId(id)
            if (compact) setShowHistory(false)
          }}
          onNew={start}
          onArchive={(id) => archive.mutate({ id, archived: true })}
          onDelete={(id) => {
            remove.mutate(id)
            if (id === activeId) setActiveId(null)
          }}
        />
      )}

      <section className="chat-main">
        <header className="chat-bar">
          {compact && (
            <button
              type="button"
              className="btn tiny"
              aria-expanded={showHistory}
              onClick={() => setShowHistory((was) => !was)}
            >
              History
            </button>
          )}
          <strong className="chat-title">
            {thread.data?.conversation.title ?? 'New conversation'}
          </strong>
          {!compact && (
            <button type="button" className="btn tiny" onClick={start}>
              <MessageSquarePlus aria-hidden="true" /> New
            </button>
          )}
        </header>

        <ContextChips
          context={context}
          onDetach={(item) => detach.mutate({ kind: item.kind, ref: item.ref })}
        />

        <div className="chat-thread" role="log" aria-label="Conversation">
          {turns.length === 0 && (
            <div className="chat-empty">
              <p>
                Grounded in this machine’s own record — strategies, backtests, verdicts and engine
                activity — through the same bounded action registry the interface uses. It will not
                predict markets or suggest trades, and every answer says how it was produced.
              </p>
              <div className="chat-suggest">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="btn tiny"
                    onClick={() => submit(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((turn) => (
            <Message key={turn.turn_id} turn={turn} />
          ))}
          {send.isPending && (
            <p className="chat-working" role="status">
              Working…
            </p>
          )}
          {send.isError && (
            <p className="chat-failed" role="alert">
              {(send.error as Error).message}
            </p>
          )}
          <div ref={bottom} />
        </div>

        <form
          className="chat-input"
          onSubmit={(event) => {
            event.preventDefault()
            submit(draft)
          }}
        >
          <input
            aria-label="Ask a question"
            placeholder="Ask about strategies, verdicts, regimes or engine state…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button className="btn primary" type="submit" disabled={send.isPending || !draft.trim()}>
            Send
          </button>
        </form>
      </section>
    </div>
  )
}
