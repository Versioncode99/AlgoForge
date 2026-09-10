import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson, putJson } from './api'

/* The funded-account rule engine, as the interface reads it.
 *
 * `Level` has five values and `not_assessed` is one of them. It is not a
 * synonym for `ok`: a rule nobody could check has to look different from a rule
 * that passed, or the reader stops looking at the ones that matter. Everything
 * downstream of this type — the tone, the ordering, the summary — turns on that
 * distinction.
 */

export type Level = 'ok' | 'caution' | 'warning' | 'breach' | 'not_assessed'

export type RuleStatus = {
  key: string
  label: string
  level: Level
  observed: number | string | null
  limit: number | string | null
  buffer: number | null
  headroom: number | null
  detail: string
}

export type AccountAssessment = {
  rules_id: string
  rules_name: string
  as_of: string
  level: Level
  can_trade: boolean
  balance: number
  equity: number
  loss_floor: number
  statuses: RuleStatus[]
  breaches: string[]
  limitations: string[]
}

export type AccountRules = {
  name: string
  provider: string
  phase: 'CHALLENGE' | 'FUNDED'
  starting_balance: number
  maximum_loss: number
  trail_mode: 'static' | 'end_of_day' | 'intraday'
  floor_cap: number | null
  daily_loss_limit: number | null
  profit_target: number | null
  max_position_contracts: number | null
  max_order_contracts: number | null
  max_open_positions: number | null
  minimum_trading_days: number | null
  consistency_share: number | null
  max_risk_per_trade: number | null
  session_windows: { label: string; opens: string; closes: string }[]
  timezone: string
  custom_limits: {
    key: string; label: string; metric: string; comparison: 'max' | 'min'; value: number
    advisory: boolean
  }[]
  source_note: string
}

export type PropAccount = {
  account_id: string
  name: string
  rules: AccountRules
  rules_id: string
  created_at: string
  updated_at: string
}

export type AccountStatus = {
  account: PropAccount
  assessment: AccountAssessment | null
  state?: Record<string, number | string | number[]>
  state_source?: string
  reason?: string
}

export const LEVEL_LABEL: Record<Level, string> = {
  ok: 'OK',
  caution: 'CAUTION',
  warning: 'WARNING',
  breach: 'BREACH',
  not_assessed: 'NOT MEASURED',
}

/** The tone each level renders in.
 *
 * `not_assessed` is `unknown`, deliberately sharing the palette the judge uses
 * for INCONCLUSIVE. One vocabulary for "we did not measure this" across the
 * whole application is worth more than a colour chosen per screen.
 */
export const LEVEL_TONE: Record<Level, 'good' | 'warn' | 'bad' | 'unknown'> = {
  ok: 'good',
  caution: 'warn',
  warning: 'warn',
  breach: 'bad',
  not_assessed: 'unknown',
}

export const usePropAccounts = () =>
  useQuery({
    queryKey: ['prop-accounts'],
    queryFn: () => getJson<{ accounts: PropAccount[]; selected: string | null }>('/prop/accounts'),
  })

export const usePropStatus = (accountId?: string | null) =>
  useQuery({
    queryKey: ['prop-status', accountId ?? 'selected'],
    refetchInterval: 30_000,
    queryFn: () =>
      getJson<AccountStatus>(
        `/prop/accounts/status${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''}`,
      ),
  })

export function usePropMutations() {
  const client = useQueryClient()
  const after = () => client.invalidateQueries()
  return {
    create: useMutation({
      mutationFn: (rules: Partial<AccountRules>) =>
        postJson<{ account: PropAccount }>('/prop/accounts', { rules }),
      onSuccess: after,
    }),
    update: useMutation({
      mutationFn: ({ id, rules }: { id: string; rules: Partial<AccountRules> }) =>
        putJson<{ account: PropAccount }>(`/prop/accounts/${id}`, { rules }),
      onSuccess: after,
    }),
    select: useMutation({
      mutationFn: (id: string) => postJson<{ selected: string }>(`/prop/accounts/${id}/select`),
      onSuccess: after,
    }),
    record: useMutation({
      mutationFn: ({ id, state, source }: { id: string; state: Record<string, unknown>; source: string }) =>
        postJson<AccountStatus>(`/prop/accounts/${id}/state`, { state, source }),
      onSuccess: after,
    }),
    replay: useMutation({
      mutationFn: ({ strategyId, accountId }: { strategyId: string; accountId?: string }) =>
        postJson<{ assessment: AccountAssessment; limitations: string[] }>(
          `/prop/accounts/replay/${encodeURIComponent(strategyId)}${
            accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''
          }`,
        ),
      onSuccess: after,
    }),
  }
}
