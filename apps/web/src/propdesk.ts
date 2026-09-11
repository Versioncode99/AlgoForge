import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from './api'

/* The Prop Desk, as the interface reads it.
 *
 * Two shapes carry the whole discipline of this feature and everything here is
 * arranged around them.
 *
 * `Permission` has four values and `unknown` is one of them. It is not a
 * synonym for "fine": a firm rule nobody has recorded blocks every automatic
 * action, and the screen has to look different for a permission somebody
 * granted and one nobody has read. Rendering them the same is how a trader
 * discovers a rule from a closed account.
 *
 * A `DeskDecision` carries the whole ladder — every rung, whether it passed,
 * and why. The refusals are the half worth drawing: "why is this follower flat
 * while the leader is long three" is the question, and it has an answer per
 * stage, in words, from the record.
 */

export type Permission = 'allowed' | 'blocked' | 'unknown' | 'requires_confirmation'

export const PERMISSION_LABEL: Record<Permission, string> = {
  allowed: 'Allowed',
  blocked: 'Blocked',
  unknown: 'Not recorded',
  requires_confirmation: 'Needs confirmation',
}

/** Tone per permission, in the shared vocabulary.
 *
 * `unknown` maps to `warn` rather than to `unknown`. That is deliberate and it
 * is the one place this file departs from the obvious mapping: elsewhere in the
 * application "unknown" means a measurement nobody took, which is neutral-ish.
 * Here it means a *rule* nobody read, and the desk refuses every automatic
 * action on it — so it is a hazard, and it has to look like one.
 */
export const PERMISSION_TONE: Record<Permission, 'good' | 'warn' | 'bad' | 'unknown'> = {
  allowed: 'good',
  blocked: 'bad',
  unknown: 'warn',
  requires_confirmation: 'warn',
}

export type ProviderDescriptor = {
  provider: string
  display_name: string
  what_it_is: string
  auth_method: string
  environments: string[]
  account_key_fields: string[]
  bracket_model: string
  rate_limit_per_minute: number | null
  session_seconds: number | null
  live_connector_implemented: boolean
  documentation_url: string
  evidence: string
  limitations: string[]
}

export type PlatformBinding = {
  platform: string
  display_name: string
  provider: string | null
  signal_source_only: boolean
  note: string
  evidence: string
}

export type RequiredWork = {
  engineering: string[]
  external: string[]
  credentials: string[]
  open_questions: string[]
}

export type ProviderCatalogue = {
  providers: ProviderDescriptor[]
  platforms: PlatformBinding[]
  data_feeds: { feed_id: string; name: string; routes_orders: false; note: string }[]
  live_connectors_implemented: string[]
  adapters: Record<string, string>
  required_work: Record<string, RequiredWork>
}

export type AdapterHealth = {
  provider: string
  connection_id: string
  state: string
  heartbeat_age_seconds: number | null
  session_expires_in_seconds: number | null
  consecutive_failures: number
  rate_budget_remaining: number | null
  queued_commands: number
  last_error: string
}

export type Connection = {
  connection_id: string
  provider: string
  environment: string
  label: string
  state: string
  platform: string | null
  last_error: string
  health: AdapterHealth | null
}

export type DeskAccountRow = {
  account_uid: string
  canonical: string
  display_name: string
  account_type: string
  connection_id: string
  balance: number | null
  equity: number | null
  prop_account_id: string | null
  policy_id: string | null
  capability: {
    order_types: string[]
    max_contracts: number | null
    can_trade: boolean | null
    bracket_model: string
  }
}

export type ProgramPolicy = {
  policy_id: string
  version: string
  firm_label: string
  program_label: string
  automation: Permission
  copy_in: Permission
  copy_out: Permission
  algorithmic_allocation: Permission
  cross_account_hedging: Permission
  third_party_copy: Permission
  order_origin: string
  permitted_products: string[] | null
  prohibited_products: string[]
  source_note: string
}

export type Follower = {
  account_uid: string
  mode: string
  active: boolean
  disabled_reason: string
  sizing: {
    method: string
    value: number
    rounding: string
    mapping: string
    target_root: string
    max_contracts_per_order: number | null
    max_position_contracts: number | null
  }
}

export type Group = {
  group_id: string
  name: string
  active: boolean
  owner_attested_by: string
  leader: { account_uid: string; source: string; products: string[] }
  followers: Follower[]
  policy: Record<string, boolean | number>
}

export type MappedQuantity = {
  source_root: string
  target_root: string
  policy: string
  rounding: string
  source_quantity: number
  exact_quantity: number
  quantity: number
  exposure_error: number | null
  capped_by: string
  detail: string
}

export type CopyDecision = {
  group_id: string
  follower_account_uid: string
  outcome: string
  detail: string
  mapping: MappedQuantity | null
  leader_position: number
  follower_position: number
  target_position: number
}

export type DeskStage = {
  stage: string
  passed: boolean
  unknown: boolean
  detail: string
}

export type DeskDecision = {
  decision_id: string
  cleared: boolean
  dispatched: boolean
  blocking_stages: string[]
  stages: DeskStage[]
  reasons: string[]
  at: string
  intent: {
    account_uid: string
    kind: string
    symbol: string
    side: string | null
    quantity: number | null
    reason: string
  }
  acknowledgement: { accepted: boolean; reason: string; duplicate: boolean } | null
}

export type CopyHealth = {
  group_id: string
  active: boolean
  followers: number
  replicating: number
  unavailable: number
  diverged: number
  quarantined: number
  healthy: boolean
  issues: string[]
}

export type Candidate = {
  account_uid: string
  strategy_id: string
  feasible: boolean
  max_contracts: number
  health_grade: string
  requires_confirmation: boolean
  score: number
  detail: string
  reasons: { check: string; passed: boolean; unknown: boolean; detail: string }[]
}

export type AllocationRow = {
  account_uid: string
  strategy_id: string
  contracts: number
  source: string
  requires_confirmation: boolean
  confirmed_by: string
  actionable: boolean
  rationale: string
  verdict_id: string
  decided_at: string
}

export type AllocationPlan = {
  plan_id: string
  allocations: AllocationRow[]
  candidates: Candidate[]
  advice_rejected: string[]
  advice_clamped: string[]
  limitations: string[]
}

export type EconomicEvent = {
  event_id: string
  title: string
  at: string
  country: string
  impact: 'high' | 'medium' | 'low' | 'unknown'
  source: string
  time_is_approximate: boolean
  note: string
}

export type NewsState = {
  events: EconomicEvent[]
  policy: {
    enabled: boolean
    minutes_before: number
    minutes_after: number
    minimum_impact: string
    action: string
  }
  assessment: {
    restricted: boolean
    action: string
    events: EconomicEvent[]
    next_event: EconomicEvent | null
    minutes_to_next: number | null
    gaps: string[]
  }
  availability: {
    provider: string
    available: boolean
    reason: string
    requires: string[]
    attribution: string
  }[]
  sources: { name: string; status: string; detail: string; url: string }[]
}

export type DeskActivity = {
  decisions: DeskDecision[]
  reconciliations: {
    account_uid: string
    trigger: string
    state: string
    at: string
    divergences: { kind: string; severity: string; detail: string; symbol: string }[]
    limitations: string[]
  }[]
  allocation_changes: {
    account_uid: string
    at: string
    kind: string
    strategy_id: string
    previous_strategy_id: string
    contracts: number
    rationale: string
  }[]
  health: AdapterHealth[]
  counts: Record<string, number>
}

const key = (...parts: unknown[]) => ['propdesk', ...parts]

export function useProviders() {
  return useQuery({
    queryKey: key('providers'),
    // A declaration, not live state: it changes when the application is rebuilt.
    staleTime: Infinity,
    queryFn: () => getJson<ProviderCatalogue>('/propdesk/providers'),
  })
}

export function useConnections() {
  return useQuery({
    queryKey: key('connections'),
    queryFn: () =>
      getJson<{ connections: Connection[]; accounts: DeskAccountRow[] }>(
        '/propdesk/connections',
      ),
  })
}

export function usePolicies() {
  return useQuery({
    queryKey: key('policies'),
    queryFn: () =>
      getJson<{
        policies: ProgramPolicy[]
        questions: { key: string; question: string }[]
        values: Permission[]
        note: string
      }>('/propdesk/policies'),
  })
}

export function useGroups() {
  return useQuery({
    queryKey: key('groups'),
    queryFn: () => getJson<{ groups: Group[] }>('/propdesk/groups'),
  })
}

export function useAllocation() {
  return useQuery({
    queryKey: key('allocation'),
    queryFn: () =>
      getJson<{
        allocations: AllocationRow[]
        constraints: Record<string, number | string[]>
        history: DeskActivity['allocation_changes']
      }>('/propdesk/allocation'),
  })
}

export function useNews(days = 7) {
  return useQuery({
    queryKey: key('news', days),
    queryFn: () => getJson<NewsState>(`/propdesk/news?days=${days}`),
  })
}

export function useDeskActivity(limit = 100) {
  return useQuery({
    queryKey: key('activity', limit),
    queryFn: () => getJson<DeskActivity>(`/propdesk/activity?limit=${limit}`),
  })
}

/** Every write, in one hook.
 *
 * They all invalidate the whole desk rather than one key: a connection changes
 * which accounts exist, a policy changes what every group may do, and an
 * allocation changes what the activity log holds. Being precise here would
 * mean maintaining a second model of what depends on what.
 */
export function useDeskMutations() {
  const client = useQueryClient()
  const refresh = { onSuccess: () => client.invalidateQueries({ queryKey: ['propdesk'] }) }

  return {
    createConnection: useMutation({
      mutationFn: (body: { provider: string; label: string; environment?: string }) =>
        postJson<{ connection: Connection; note: string }>('/propdesk/connections', body),
      ...refresh,
    }),
    connect: useMutation({
      mutationFn: (connectionId: string) =>
        postJson<{ connected: boolean; reason?: string }>(
          `/propdesk/connections/${connectionId}/connect`,
        ),
      ...refresh,
    }),
    disconnect: useMutation({
      mutationFn: (connectionId: string) =>
        postJson(`/propdesk/connections/${connectionId}/disconnect`),
      ...refresh,
    }),
    seedSimulator: useMutation({
      mutationFn: ({ connectionId, ...body }: { connectionId: string; account_id: string; balance?: number }) =>
        postJson(`/propdesk/connections/${connectionId}/simulated-accounts`, body),
      ...refresh,
    }),
    savePolicy: useMutation({
      mutationFn: (policy: Record<string, unknown>) =>
        postJson<{ policy: ProgramPolicy }>('/propdesk/policies', policy),
      ...refresh,
    }),
    linkPolicy: useMutation({
      mutationFn: ({ accountUid, policyId }: { accountUid: string; policyId: string | null }) =>
        postJson(`/propdesk/accounts/${accountUid}/policy`, { policy_id: policyId }),
      ...refresh,
    }),
    linkRules: useMutation({
      mutationFn: ({ accountUid, propAccountId }: { accountUid: string; propAccountId: string | null }) =>
        postJson(`/propdesk/accounts/${accountUid}/rules`, { prop_account_id: propAccountId }),
      ...refresh,
    }),
    createGroup: useMutation({
      mutationFn: (body: { name: string; leader_account_uid: string; attested_by: string }) =>
        postJson<{ group: Group }>('/propdesk/groups', body),
      ...refresh,
    }),
    addFollower: useMutation({
      mutationFn: ({ groupId, ...body }: { groupId: string; account_uid: string; sizing?: Record<string, unknown> }) =>
        postJson<{ group: Group }>(`/propdesk/groups/${groupId}/followers`, body),
      ...refresh,
    }),
    setGroupActive: useMutation({
      mutationFn: ({ groupId, active }: { groupId: string; active: boolean }) =>
        postJson<{ group: Group }>(`/propdesk/groups/${groupId}/active`, { active }),
      ...refresh,
    }),
    copyPass: useMutation({
      mutationFn: ({ groupId, ...body }: {
        groupId: string
        symbol: string
        leader_position: number
        dry_run?: boolean
      }) =>
        postJson<{
          copy_decisions: CopyDecision[]
          desk_decisions: DeskDecision[]
          health: CopyHealth
          dry_run: boolean
        }>(`/propdesk/groups/${groupId}/pass`, body),
      ...refresh,
    }),
    reconcile: useMutation({
      mutationFn: ({ accountUid, trigger }: { accountUid: string; trigger?: string }) =>
        postJson(`/propdesk/accounts/${accountUid}/reconcile`, { trigger: trigger ?? 'periodic' }),
      ...refresh,
    }),
    planAllocation: useMutation({
      mutationFn: (body: { strategy_ids: string[]; advice?: Record<string, unknown>[] }) =>
        postJson<{ plan: AllocationPlan }>('/propdesk/allocation/plan', body),
    }),
    applyAllocation: useMutation({
      mutationFn: (body: { allocations: AllocationRow[]; actor: string }) =>
        postJson('/propdesk/allocation/apply', body),
      ...refresh,
    }),
    recordEvent: useMutation({
      mutationFn: (body: { title: string; at: string; impact?: string }) =>
        postJson('/propdesk/news/events', body),
      ...refresh,
    }),
    saveNewsPolicy: useMutation({
      mutationFn: (policy: Record<string, unknown>) => postJson('/propdesk/news/policy', policy),
      ...refresh,
    }),
  }
}
