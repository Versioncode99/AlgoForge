import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from './api'

/* The workstation context, and the one way to run a registered verb.
 *
 * Two things live here, and they are the same thing seen from two sides.
 *
 * **What is being looked at.** An instrument, a timeframe, a campaign, a
 * strategy, an account — held per link group on the workspace, so reopening
 * "NQ Lab" tomorrow reopens it on NQ. Setting a context writes nothing to any
 * panel: a panel in the group displays the context, a panel outside it keeps
 * its own symbol, and unlinking reveals the symbol it was always pinned to.
 *
 * **How anything gets done.** `runAction` posts to the action registry, which
 * is the same 149-verb surface the assistant calls and the same one the mode
 * permissions are evaluated against. The command palette uses it, so a command
 * typed into the palette and the same request typed at the assistant take one
 * path — and a verb the registry does not have cannot be offered by either.
 */

export type ContextFacets = {
  instrument: string
  timeframe: string
  dataset: string
  campaign: string
  strategy: string
  account: string
}

export type ResolvedPanel = {
  panel_id: string
  symbol: string
  timeframe: string
  group: string | null
  /** `context` when the value was followed, `panel` when it is the panel's own. */
  source: 'panel' | 'context'
}

export type ContextView = {
  workspace_id: string
  context: ContextFacets
  groups: Record<string, ContextFacets>
  panel_groups: string[]
  panels: ResolvedPanel[]
}

export type Instrument = {
  root: string
  exchange: string
  description: string
  multiplier: number
  tick_size: number
  tick_value: number
  product_group: string
  /** Whether the contract specification was checked against the exchange. */
  specification: 'verified' | 'unverified'
  source_note: string
}

export type ActionSchema = {
  name: string
  description: string
  mutating: boolean
  risk: 'safe' | 'confirm' | 'high'
  protected: boolean
  requires_confirmation: boolean
  parameters: { type: string; properties: Record<string, unknown>; required: string[] }
}

export type CampaignRow = {
  campaign_id: string
  name: string
  status: string
  objective: string
  priority: number
}

/** Run a registered action. The only way this interface performs a verb. */
export function runAction<T = Record<string, unknown>>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  return postJson<T>(`/actions/${name}`, { arguments: args })
}

export function useActionSchemas(enabled = true) {
  return useQuery({
    queryKey: ['action-schemas'],
    enabled,
    // The registry is fixed for the life of the process, so this is fetched
    // once and kept. Re-fetching it on every palette open was a request per
    // keystroke-adjacent event for a list that cannot change.
    staleTime: Infinity,
    queryFn: () => getJson<ActionSchema[]>('/actions'),
  })
}

export function useInstruments(enabled = true) {
  return useQuery({
    queryKey: ['instruments'],
    enabled,
    staleTime: Infinity,
    queryFn: async () => {
      const payload = await runAction<{ instruments?: Instrument[] }>('list_instruments')
      // `?? []` rather than trusting the shape: react-query treats an
      // undefined return as a programming error and throws, which would take
      // the palette down over a payload it could simply have shown as empty.
      return payload.instruments ?? []
    },
  })
}

export function useCampaigns(enabled = true) {
  return useQuery({
    queryKey: ['campaign-list'],
    enabled,
    queryFn: async () => {
      const payload = await runAction<{ campaigns?: CampaignRow[] }>('list_campaigns')
      return payload.campaigns ?? []
    },
  })
}

export function useWorkstationContext() {
  return useQuery({
    queryKey: ['workstation-context'],
    queryFn: () => runAction<ContextView>('describe_context'),
    // A context is only ever changed by an action this client performs, or by
    // the assistant. Polling it every few seconds would be a request per
    // second per open tab for a value that changes a handful of times an hour.
    staleTime: 30_000,
    // A workspace may genuinely have none open; that is not an error state.
    retry: false,
  })
}

export function useSetContext() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (facets: Partial<ContextFacets> & { group?: string }) =>
      runAction<Record<string, unknown>>('set_context', {
        ...(facets.instrument !== undefined && { instrument: facets.instrument }),
        ...(facets.timeframe !== undefined && { timeframe: facets.timeframe }),
        ...(facets.dataset !== undefined && { dataset: facets.dataset }),
        ...(facets.campaign !== undefined && { campaign_id: facets.campaign }),
        ...(facets.strategy !== undefined && { strategy_id: facets.strategy }),
        ...(facets.account !== undefined && { account_id: facets.account }),
        ...(facets.group ? { group: facets.group } : {}),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workstation-context'] })
      // The workspace itself changed, so anything drawing panels is stale too.
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
      void queryClient.invalidateQueries({ queryKey: ['active-workspace'] })
    },
  })
}

export function useClearContext() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { facet?: keyof ContextFacets; group?: string }) =>
      runAction<Record<string, unknown>>('clear_context', {
        ...(body.facet ? { facet: body.facet } : {}),
        ...(body.group ? { group: body.group } : {}),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workstation-context'] })
      void queryClient.invalidateQueries({ queryKey: ['active-workspace'] })
    },
  })
}

/** What a panel should display, given the resolved set. Never a guess.
 *
 * Returns `null` rather than a fallback symbol when the panel is not in the
 * resolved list: a caller that cannot tell "no symbol" from "we did not look"
 * will eventually render one as the other.
 */
export function symbolFor(panelId: string, resolved: ResolvedPanel[]): ResolvedPanel | null {
  return resolved.find((item) => item.panel_id === panelId) ?? null
}
