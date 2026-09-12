import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson, deleteJson } from './api'

/* The research fabric, as the interface sees it.
 *
 * Every shape here mirrors a store on the backend and nothing is derived on the
 * client. That is deliberate: a number computed twice is a number that will
 * disagree with itself on the day it matters, and the whole point of this work
 * was that "Skipped by memory: 1,290" could not be reconciled with anything.
 */

export type CampaignStatus = 'created' | 'running' | 'paused' | 'stopped' | 'completed'

export type CampaignProgress = {
  experiments: number
  hypotheses: number
  families_created: number
  templates_created: number
  mechanisms: number
  duplicates_rejected: number
  blocked_proposals: number
  consecutive_duplicates: number
  failures: number
  promising: number
  validation_candidates: number
  validated: number
  inconclusive: number
  sources_retrieved: number
  followups_generated: number
  compute_units: number
  spend: Record<string, number>
}

export type Campaign = {
  campaign_id: string
  name: string
  objective: string
  dataset: string
  symbol: string
  timeframe: string
  status: CampaignStatus
  progress: CampaignProgress
  exhausted: boolean
  exhausted_reason: string
  stopped_reason: string
  created_at: string
  updated_at: string
  description: string
  priority: number
  agent_target: number
  archived: boolean
  parent_campaign_id: string
  tags: string[]
}

export type AgentRole =
  | 'DISCOVERY' | 'LITERATURE' | 'FEATURE' | 'HYPOTHESIS' | 'FALSIFICATION'
  | 'REGIME' | 'ROBUSTNESS' | 'VALIDATION' | 'REVIEWER' | 'SPECIALIST'

export type AgentState =
  | 'CREATED' | 'STARTING' | 'RUNNING' | 'IDLE' | 'WAITING'
  | 'BLOCKED' | 'COMPLETED' | 'FAILED' | 'STOPPED'

export type ResearchAgent = {
  agent_id: string
  campaign_id: string
  role: AgentRole
  role_purpose: string
  name: string
  objective: string
  state: AgentState
  current_task: string
  current_claim: string
  compute_budget: number
  compute_spent: number
  experiments: number
  findings: number
  errors: number
  heartbeat_at: string
  progress_at: string
  stale: boolean
  exhausted: boolean
  last_result: string
  last_error: string
  created_at: string
  updated_at: string
}

export type AgentCounts = {
  total: number
  eligible: number
  stale: number
  experiments: number
  findings: number
  errors: number
  by_state: Record<AgentState, number>
  by_role: Record<AgentRole, number>
}

export type CampaignRuntime = {
  campaign_id: string
  workers: number
  health: number
  /** Cycles behind `health`. Zero means it is a scheduling prior, not a measurement. */
  health_observed: number
  barren_run: number
  stalled: boolean
  last_progress_at: string
  assigned: number
  recent_outcomes: Record<string, number>
}

export type ControlCenter = {
  totals: {
    campaigns: number
    running: number
    experiments: number
    hypotheses: number
    mechanisms: number
    families_created: number
    templates_created: number
    followups: number
    compute_units: number
  }
  campaigns: Campaign[]
  allocation: {
    engine_state: string | null
    running_campaigns: {
      campaign_id: string
      name: string
      priority: number
      dataset: string
      symbol: string
      agent_target: number
      runtime: CampaignRuntime
      agents: AgentCounts
      progress: CampaignProgress
    }[]
    worker_plan: string[]
    stalled: string[]
  }
  agents: AgentCounts
  skips: {
    total: number
    distinct: number
    useful: number
    wasted: number
    neutral: number
    by_kind: Record<string, number>
    by_level: Record<string, number>
  }
  /** Attempts, passes, failures and blocked — separately.
   *
   *  "Validation: 0" cannot distinguish "nothing was eligible" from "everything
   *  was tried and everything failed", and those are opposite facts about a
   *  campaign. */
  validation: {
    attempts: number
    passed: number
    failed: number
    inconclusive: number
    blocked: number
    pending: number
  }
  frontier: Record<string, number>
  capacity: { max_agents: number; ceiling: number }
}

const KEY = ['research', 'control-center'] as const

export function useControlCenter(refetchMs = 5_000) {
  return useQuery({
    queryKey: KEY,
    refetchInterval: refetchMs,
    queryFn: () => getJson<ControlCenter>('/campaigns/control-center'),
  })
}

export function useCampaignAgents(campaignId: string | null) {
  return useQuery({
    queryKey: ['campaign-agents', campaignId],
    enabled: !!campaignId,
    refetchInterval: 5_000,
    queryFn: () =>
      getJson<{
        agents: ResearchAgent[]
        counts: AgentCounts
        claims: { claim_id: string; subject: string; agent_id: string; replica_of: string; expired: boolean }[]
        roles: { role: AgentRole; purpose: string }[]
      }>(`/campaigns/${campaignId}/agents`),
  })
}

export function useCampaignSkips(campaignId: string | null) {
  return useQuery({
    queryKey: ['campaign-skips', campaignId],
    enabled: !!campaignId,
    queryFn: () =>
      getJson<{
        counts: ControlCenter['skips']
        skips: {
          skip_id: string; kind: string; level: string; subject: string; reason: string
          matched: string | null; similarity: number | null; retry_permitted: boolean
          retry_condition: string; occurrences: number; created_at: string
        }[]
        retryable: { subject: string; reason: string; retry_condition: string }[]
      }>(`/campaigns/${campaignId}/skips`),
  })
}

function invalidator(client: ReturnType<typeof useQueryClient>) {
  return () => {
    client.invalidateQueries({ queryKey: KEY })
    client.invalidateQueries({ queryKey: ['campaigns'] })
  }
}

export function useCampaignControl() {
  const client = useQueryClient()
  const refresh = invalidator(client)
  return {
    start: useMutation({
      mutationFn: (body: { campaign_id: string; workers?: number }) =>
        postJson(`/campaigns/${body.campaign_id}/start`, { workers: body.workers ?? 4 }),
      onSuccess: refresh,
    }),
    pause: useMutation({
      mutationFn: (campaignId: string) => postJson(`/campaigns/${campaignId}/pause`, {}),
      onSuccess: refresh,
    }),
    stop: useMutation({
      mutationFn: (campaignId: string) => postJson(`/campaigns/${campaignId}/stop`, {}),
      onSuccess: refresh,
    }),
    duplicate: useMutation({
      mutationFn: (body: { campaign_id: string; name?: string }) =>
        postJson(`/campaigns/${body.campaign_id}/duplicate`, { name: body.name ?? '' }),
      onSuccess: refresh,
    }),
    archive: useMutation({
      mutationFn: (campaignId: string) => postJson(`/campaigns/${campaignId}/archive`, {}),
      onSuccess: refresh,
    }),
    prioritise: useMutation({
      mutationFn: (body: { campaign_id: string; priority: number }) =>
        postJson(`/campaigns/${body.campaign_id}/priority`, { priority: body.priority }),
      onSuccess: refresh,
    }),
    deployAgents: useMutation({
      mutationFn: (body: { campaign_id: string; count: number; roles?: string[] }) =>
        postJson<{ created: ResearchAgent[]; counts: AgentCounts; capacity_note: string }>(
          `/campaigns/${body.campaign_id}/agents`,
          { count: body.count, roles: body.roles ?? [] },
        ),
      onSuccess: refresh,
    }),
    removeAgent: useMutation({
      mutationFn: (body: { campaign_id: string; agent_id: string }) =>
        deleteJson(`/campaigns/${body.campaign_id}/agents/${body.agent_id}`),
      onSuccess: refresh,
    }),
  }
}

/** How each agent state should read. Not a traffic light: COMPLETED is an
 *  outcome, not a fault, and colouring it red teaches people that finishing is
 *  failing. */
export const AGENT_TONE: Record<AgentState, 'good' | 'warn' | 'bad' | 'plain'> = {
  CREATED: 'plain',
  STARTING: 'plain',
  RUNNING: 'good',
  IDLE: 'warn',
  WAITING: 'warn',
  BLOCKED: 'bad',
  COMPLETED: 'plain',
  FAILED: 'bad',
  STOPPED: 'plain',
}
