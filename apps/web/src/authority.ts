import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from './api'

/* What an assistant may do on the operator's behalf.
 *
 * This is the half of the old mode system that had to survive it. A mode
 * decided navigation *and* authority, and removing the chooser removes the
 * first job's reason to exist while saying nothing at all about the second.
 * So it is set explicitly, in Settings, in one place, and the wording here is
 * the wording the backend computes — a permissions screen that can disagree
 * with the enforcement is worse than none.
 */

export type AuthorityProfile = {
  unattended_work: boolean
  unattended_execution: boolean
  label: string
  summary: string
  /** The mode and stance this profile stands for, so an audit entry written
   *  today is comparable with one written before the chooser was removed. */
  equivalent_mode: string
  equivalent_stance: string | null
}

export type Policy = {
  mode: string
  stance: string | null
  summary: string
  always_denied_to_ai: string[]
}

export type AuthorityState = {
  profile: AuthorityProfile
  available: AuthorityProfile[]
  policy: Policy
}

export function useAuthority() {
  return useQuery({
    queryKey: ['authority'],
    queryFn: () => getJson<AuthorityState>('/authority'),
  })
}

export function useSetAuthority() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (grant: { unattended_work: boolean; unattended_execution: boolean }) =>
      postJson<AuthorityState>('/authority', grant),
    // Everything: what an assistant may do is drawn in more places than it is
    // stored, and a stale copy of it is the one kind of stale copy that matters.
    onSuccess: () => client.invalidateQueries(),
  })
}
