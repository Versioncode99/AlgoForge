import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from './api'

/* The four operating environments, as the interface sees them.
 *
 * Every shape here mirrors what `forge.modes` declares. The shell does not hold
 * its own copy of the navigation, the taglines or the limitations: it renders
 * whatever the manifest says, so a section added in the backend appears here
 * without an edit, and one removed cannot linger in a rail pointing at nothing.
 */

export type ModeKey = 'normal' | 'prop_firm' | 'ai' | 'hedge_fund'
export type Stance = 'human_in_the_loop' | 'autonomous'

export type Section = {
  route: string
  label: string
  detail: string
  group: string
  panel_kinds: string[]
}

export type ModeDescriptor = {
  mode: ModeKey
  name: string
  tagline: string
  purpose: string
  workspace_template: string
  sections: Section[]
  stances: Stance[]
  default_stance: Stance | null
  limitations: string[]
}

export type LoopStage = {
  stage: string
  label: string
  purpose: string
  route: string
  produces: string
}

export type ModeSession = {
  mode: ModeKey | null
  stance: Stance | null
  workspace_id: string | null
}

export type Policy = {
  mode: string
  stance: string | null
  summary: string
  always_denied_to_ai: string[]
}

export type ModeState = {
  session: ModeSession
  descriptor: ModeDescriptor
  policy: Policy
  /** False before a mode has been chosen: the descriptor is then a default the
   *  policy falls back to, not a mode the operator is standing in. Rendering it
   *  as the open mode is how a home screen quietly becomes AI mode. */
  policy_applies: boolean
}

export const STANCE_LABEL: Record<Stance, string> = {
  human_in_the_loop: 'Human in the loop',
  autonomous: 'Autonomous',
}

export const STANCE_DETAIL: Record<Stance, string> = {
  human_in_the_loop:
    'AI researches, constructs portfolios and prepares orders. You approve anything that reaches the book.',
  autonomous:
    'AI runs the loop unattended — inside the risk engine, the pre-trade gate and the kill switch, none of which it can change.',
}

export function useModes() {
  return useQuery({
    queryKey: ['modes'],
    // The manifest is a declaration, not live state. Refetching it every fifteen
    // seconds would be four requests a minute for a value that changes when the
    // application is rebuilt.
    staleTime: Infinity,
    queryFn: () => getJson<{ modes: ModeDescriptor[]; loop: LoopStage[] }>('/modes'),
  })
}

export function useModeSession() {
  return useQuery({
    queryKey: ['mode-session'],
    queryFn: () => getJson<ModeState>('/modes/session'),
  })
}

/** Enter a mode, or change the stance of the one that is open.
 *
 * Both invalidate everything: entering a mode opens a different workspace, and
 * changing the stance changes what an agent may do, which is drawn in more
 * places than it is stored.
 */
export function useEnterMode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ mode, stance }: { mode: ModeKey; stance?: Stance | null }) =>
      postJson<{ session: ModeSession }>(`/modes/${mode}/enter`, { stance: stance ?? null }),
    onSuccess: () => client.invalidateQueries(),
  })
}

export function useSetStance() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (stance: Stance) => postJson<{ session: ModeSession }>('/modes/stance', { stance }),
    onSuccess: () => client.invalidateQueries(),
  })
}

export function useLeaveMode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => postJson<{ session: ModeSession }>('/modes/leave'),
    onSuccess: () => client.invalidateQueries(),
  })
}
