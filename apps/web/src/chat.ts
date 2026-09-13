import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteJson, getJson, patchJson, postJson } from './api'

/* The conversation surface, as the interface sees it.
 *
 * Every shape here mirrors `forge.conversation`. The important one is
 * `provenance`, which the interface must render rather than smooth over: a
 * user's stated belief, a model's paraphrase and a judged result look the same
 * in a chat bubble, and the whole reason the backend tracks which is which is
 * so this layer can show it.
 */

export type Provenance = 'user_statement' | 'model_prose' | 'action_result' | 'deterministic'
export type Outcome = 'ok' | 'refused' | 'failed'

export type ContextKind =
  | 'strategy'
  | 'experiment'
  | 'backtest'
  | 'dataset'
  | 'account'
  | 'workspace'
  | 'chart'
  | 'finding'
  | 'campaign'

export type ArtifactKind =
  | 'strategy'
  | 'backtest'
  | 'regime'
  | 'resample'
  | 'parameter_surface'
  | 'prop_simulation'
  | 'validation'
  | 'evidence'
  | 'analysis'
  | 'workspace'
  | 'port'

export type ToolCall = {
  name: string
  arguments: Record<string, unknown>
  outcome: Outcome
  reason: string
  duration_ms: number
  result_ref: string
}

export type Artifact = {
  artifact_id: string
  kind: ArtifactKind
  title: string
  refs: Record<string, string>
  provenance: Provenance
}

export type AttachedContext = {
  kind: ContextKind
  ref: string
  label: string
  attached_at: string
}

export type Turn = {
  turn_id: string
  conversation_id: string
  role: 'user' | 'assistant'
  text: string
  created_at: string
  provenance: Provenance
  model: string
  tool_calls: ToolCall[]
  artifacts: Artifact[]
}

export type Conversation = {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  archived: boolean
  turn_count: number
  context: AttachedContext[]
  last_message: string
}

export type Thread = { conversation: Conversation; turns: Turn[] }

/** What a standing means, spelled out where it is shown.
 *
 * Four states rather than a confidence score, because the differences are
 * categorical. A model's sentence is not a weak measurement of a judged one.
 */
export const STANDING: Record<Provenance, { label: string; detail: string }> = {
  user_statement: {
    label: 'You said',
    detail: 'What you told the assistant. A stated belief is not an observation.',
  },
  model_prose: {
    label: 'Model',
    detail:
      'The model wrote this sentence. Actions may have supplied every figure in it — the ' +
      'figures are below, and the sentence is still the model’s.',
  },
  action_result: {
    label: 'Action result',
    detail: 'The output of a registered action, rendered as it came back.',
  },
  deterministic: {
    label: 'Deterministic',
    detail: 'Produced by AlgoForge itself, with no model involved, and reproducible from its inputs.',
  },
}

export function useConversations(query: string, includeArchived = false) {
  return useQuery({
    queryKey: ['conversations', query, includeArchived],
    queryFn: () => {
      const params = new URLSearchParams()
      if (query) params.set('query', query)
      if (includeArchived) params.set('include_archived', 'true')
      const suffix = params.toString()
      return getJson<Conversation[]>(`/conversations${suffix ? `?${suffix}` : ''}`)
    },
  })
}

export function useThread(conversationId: string | null) {
  return useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => getJson<Thread>(`/conversations/${conversationId}`),
    enabled: Boolean(conversationId),
  })
}

export function useCreateConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (title: string = '') => postJson<Conversation>('/conversations', { title }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

/**
 * Send a message to a named conversation.
 *
 * The conversation id travels in the *variables*, not in a closure over
 * component state. It used to be a parameter, and the first message of a new
 * thread was therefore posted to `/conversations/null/messages`: the caller
 * creates a conversation, calls `setActiveId`, and sends — all before React has
 * re-rendered, so the mutation still held the id from the previous render,
 * which was null. The request 404s and the message is simply gone.
 *
 * Nothing about that is visible in a unit test whose fetch mock accepts any
 * path ending in `/messages`. It took driving the real interface to see it.
 */
export function useSendMessage() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ conversationId, message }: { conversationId: string; message: string }) =>
      postJson<{ turn: Turn; conversation: Conversation; note: string | null }>(
        `/conversations/${conversationId}/messages`,
        { message },
      ),
    onSuccess: (_result, { conversationId }) => {
      client.invalidateQueries({ queryKey: ['conversation', conversationId] })
      client.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}

export function useRenameConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      patchJson<Conversation>(`/conversations/${id}`, { title }),
    onSuccess: (_, { id }) => {
      client.invalidateQueries({ queryKey: ['conversations'] })
      client.invalidateQueries({ queryKey: ['conversation', id] })
    },
  })
}

export function useArchiveConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      postJson<Conversation>(`/conversations/${id}/archive?archived=${archived}`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

export function useDeleteConversation() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteJson<unknown>(`/conversations/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['conversations'] }),
  })
}

export function useAttachContext(conversationId: string | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (item: { kind: ContextKind; ref: string; label?: string }) =>
      postJson<AttachedContext>(`/conversations/${conversationId}/context`, item),
    onSuccess: () => client.invalidateQueries({ queryKey: ['conversation', conversationId] }),
  })
}

export function useDetachContext(conversationId: string | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, ref }: { kind: ContextKind; ref: string }) =>
      deleteJson<unknown>(
        `/conversations/${conversationId}/context/${kind}/${encodeURIComponent(ref)}`,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ['conversation', conversationId] }),
  })
}
