import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson, deleteJson } from './api'

/* Workspaces as compositions, and the rail as one of the things composed.
 *
 * The problem this file exists to solve, stated as a user would: "I have to
 * switch to Prop Firm to see my accounts and to AI to see my research, and
 * switching throws away the screen I had." Navigation used to be a constant of
 * the mode — `forge.modes` declares four fixed section lists and the shell
 * renders whichever belongs to the session's mode.
 *
 * So the rail is now part of the workspace, and the catalogue of destinations
 * is the union of every mode's. Every mutation here posts to the action
 * registry the assistant also calls: there is no interface-only endpoint, which
 * is what stops "add the agent monitor below research" typed at the assistant
 * from diverging from the same thing done by hand.
 */

export type SidebarDestination = {
  route: string
  label: string
  detail: string
  group: string
  panel_kinds: string[]
  /** Which built-in modes offer it. Provenance — never a restriction. */
  modes: string[]
}

export type SidebarItemView = {
  route: string
  label: string
  renamed: boolean
  detail: string
  pinned: boolean
  hidden: boolean
  panel_kinds: string[]
}

export type SidebarGroupView = {
  group_id: string
  label: string
  collapsed: boolean
  items: SidebarItemView[]
}

export type SidebarView = { groups: SidebarGroupView[] }

export type WorkspaceKind = 'BUILT_IN' | 'USER_CREATED' | 'CLONED'

export type WorkspaceView = {
  workspace_id: string
  name: string
  template_key: string | null
  updated_at: string
  panels: unknown[]
  description: string
  icon: string
  kind: WorkspaceKind
  pinned: boolean
  mode: string
  campaign_ids: string[]
  account_ids: string[]
  sidebar: SidebarView
  /** False when the rail is the mode's default rather than one the operator
   *  built. The distinction matters for "restore default layout": there has to
   *  be something to restore *to*. */
  sidebar_is_custom: boolean
}

export type WorkspaceSummary = {
  workspace_id: string
  name: string
  template_key: string | null
  panel_count: number
  updated_at: string
  description: string
  icon: string
  kind: WorkspaceKind
  pinned: boolean
  mode: string
  campaign_ids: string[]
  account_ids: string[]
}

const WORKSPACES = ['workspaces'] as const

export function useWorkspaces() {
  return useQuery({
    queryKey: WORKSPACES,
    queryFn: () =>
      getJson<{ count: number; active: string | null; default: string | null; workspaces: WorkspaceSummary[] }>(
        '/workspaces',
      ),
  })
}

export function useActiveWorkspace() {
  return useQuery({
    queryKey: ['workspace', 'active'],
    queryFn: () => getJson<WorkspaceView | null>('/workspaces/active'),
  })
}

export function useSidebarDestinations() {
  return useQuery({
    queryKey: ['sidebar-destinations'],
    // The catalogue changes when the application is rebuilt, not while it runs.
    staleTime: Infinity,
    queryFn: () => getJson<{ count: number; destinations: SidebarDestination[] }>('/sidebar/destinations'),
  })
}

/** Every sidebar mutation, as one hook.
 *
 *  One hook rather than fifteen because they all invalidate the same two
 *  queries, and fifteen copies of that invalidation is fifteen chances to
 *  forget one and leave the rail showing the state before the edit. */
export function useSidebarEdit(workspaceId: string | undefined) {
  const client = useQueryClient()
  const base = `/workspaces/${workspaceId}/sidebar`
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['workspace', 'active'] })
    client.invalidateQueries({ queryKey: WORKSPACES })
  }
  return {
    addItem: useMutation({
      mutationFn: (body: { route: string; group_id?: string | null; label?: string; position?: number | null }) =>
        postJson<WorkspaceView>(`${base}/items`, body),
      onSuccess: refresh,
    }),
    removeItem: useMutation({
      mutationFn: (route: string) => deleteJson<WorkspaceView>(`${base}/items/${route}`),
      onSuccess: refresh,
    }),
    moveItem: useMutation({
      mutationFn: (body: { route: string; group_id: string; position?: number | null }) =>
        postJson<WorkspaceView>(`${base}/items/${body.route}/move`, {
          group_id: body.group_id,
          position: body.position ?? null,
        }),
      onSuccess: refresh,
    }),
    renameItem: useMutation({
      mutationFn: (body: { route: string; label: string }) =>
        postJson<WorkspaceView>(`${base}/items/${body.route}/rename`, { name: body.label }),
      onSuccess: refresh,
    }),
    pinItem: useMutation({
      mutationFn: (body: { route: string; pinned: boolean }) =>
        postJson<WorkspaceView>(`${base}/items/${body.route}/pin`, { value: body.pinned }),
      onSuccess: refresh,
    }),
    hideItem: useMutation({
      mutationFn: (body: { route: string; hidden: boolean }) =>
        postJson<WorkspaceView>(`${base}/items/${body.route}/hide`, { value: body.hidden }),
      onSuccess: refresh,
    }),
    addGroup: useMutation({
      mutationFn: (body: { group_id: string; label: string }) =>
        postJson<WorkspaceView>(`${base}/groups`, body),
      onSuccess: refresh,
    }),
    removeGroup: useMutation({
      mutationFn: (groupId: string) => deleteJson<WorkspaceView>(`${base}/groups/${groupId}`),
      onSuccess: refresh,
    }),
    renameGroup: useMutation({
      mutationFn: (body: { group_id: string; label: string }) =>
        postJson<WorkspaceView>(`${base}/groups/${body.group_id}/rename`, { name: body.label }),
      onSuccess: refresh,
    }),
    collapseGroup: useMutation({
      mutationFn: (body: { group_id: string; collapsed: boolean }) =>
        postJson<WorkspaceView>(`${base}/groups/${body.group_id}/collapse`, { value: body.collapsed }),
      onSuccess: refresh,
    }),
    reorderGroups: useMutation({
      mutationFn: (groupIds: string[]) =>
        postJson<WorkspaceView>(`${base}/groups/order`, { group_ids: groupIds }),
      onSuccess: refresh,
    }),
    reset: useMutation({
      mutationFn: () => postJson<WorkspaceView>(`${base}/reset`, {}),
      onSuccess: refresh,
    }),
  }
}

export function useCreateWorkspace() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      name: string
      template_key?: string | null
      activate?: boolean
      description?: string
      icon?: string
      mode?: string
      sidebar_items?: string[]
    }) => postJson<WorkspaceView>('/workspaces', body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: WORKSPACES })
      client.invalidateQueries({ queryKey: ['workspace', 'active'] })
    },
  })
}

export function useOpenWorkspace() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (workspaceId: string) => postJson<WorkspaceView>(`/workspaces/${workspaceId}/open`, {}),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: WORKSPACES })
      client.invalidateQueries({ queryKey: ['workspace', 'active'] })
    },
  })
}

export function usePinWorkspace() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { workspace_id: string; pinned: boolean }) =>
      postJson<WorkspaceView>(`/workspaces/${body.workspace_id}/pin`, { value: body.pinned }),
    onSuccess: () => client.invalidateQueries({ queryKey: WORKSPACES }),
  })
}

/** Group a catalogue by its declared group, preserving catalogue order.
 *  Used by the "add an item" picker so it reads like the rail it feeds. */
export function byGroup(destinations: SidebarDestination[]): { group: string; items: SidebarDestination[] }[] {
  const out: { group: string; items: SidebarDestination[] }[] = []
  for (const destination of destinations) {
    const existing = out.find((entry) => entry.group === destination.group)
    if (existing) existing.items.push(destination)
    else out.push({ group: destination.group, items: [destination] })
  }
  return out
}
