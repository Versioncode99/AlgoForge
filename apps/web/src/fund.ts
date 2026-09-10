import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson } from './api'

/* The fund's read models, and the mutations that move it along the loop.
 *
 * Every optional number here is optional on purpose. The backend reports `null`
 * for a quantity nobody measured — a volatility with no covariance estimate
 * behind it, a Sharpe over a series too short to have one — and the interface
 * has to render that as "not measured" rather than as a zero. A type that made
 * these `number` would erase the distinction before a component ever saw it.
 */

export type RiskMeasure = {
  key: string
  label: string
  value: number | null
  ceiling: number | null
  method: string
  note: string
}

export type RiskAssessment = {
  limits_name: string
  within_limits: boolean
  enabled: boolean
  measures: RiskMeasure[]
  breaches: { limit: string; observed: number; ceiling: number; detail: string }[]
  exposures: Record<string, number>
  by_strategy: Record<string, number>
  limitations: string[]
}

export type StageState = {
  stage: string
  label: string
  purpose: string
  route: string
  produces: string
  status: 'idle' | 'running' | 'ready' | 'warning' | 'blocked' | 'halted' | 'unknown'
  summary: string
  count: number | null
  detail: string
}

export type FundState = {
  nav: number
  cash: number
  capital: number
  realised_pnl: number
  gross_exposure: number
  net_exposure: number
  leverage: number
  risk: RiskAssessment
  execution_mode: string
  simulated: boolean
  stance: string | null
  stages: StageState[]
  limitations: string[]
}

export type Holding = {
  symbol: string
  weight: number
  target_notional: number
  contracts: number | null
  strategy_ids: string[]
  expected_return: number
}

export type PortfolioProposal = {
  portfolio_id: string
  capital: number
  holdings: Holding[]
  exposures: {
    gross: number; net: number; long: number; short: number; leverage: number
    by_sector: Record<string, number>; by_region: Record<string, number>
    by_asset_class: Record<string, number>; concentration: number
  }
  expected_return: number
  expected_volatility: number
  expected_sharpe: number | null
  turnover: number
  estimated_cost: number
  constraints: {
    name: string; limit: number; observed: number; binding: boolean; applied: boolean; detail: string
  }[]
  excluded: { symbol: string; strategy_id: string; reason: string }[]
  covariance_method: string
  observations: number
  limitations: string[]
  optimiser: string
  feasible: boolean
}

export type GateCheck = { check: string; status: 'pass' | 'fail' | 'not_applicable'; detail: string }
export type ScreenedOrder = {
  order: {
    order_id: string; symbol: string; side: string; quantity: number
    order_type: string; strategy_id: string; reference_price: number | null
  }
  decision: {
    order_id: string; symbol: string; decision: 'allow' | 'block'
    checks: GateCheck[]; reasons: string[]; evaluated_at: string
    clearance: { clearance_id: string } | null
  }
}

export type Approval = {
  request_id: string
  created_at: string
  expires_at: string
  requested_by: string
  origin: string
  mode: string
  stance: string
  action: string
  arguments: Record<string, unknown>
  reason: string
  summary: string
  status: string
  decided_at: string | null
  decided_by: string
  note: string
  result: string
  error: string
  expired: boolean
}

export type AuditEntry = {
  entry_id: string
  at: string
  actor: string
  origin: string
  mode: string
  stance: string
  action: string
  arguments: string
  ruling: string
  ruling_reason: string
  outcome: string
  result: string
  error: string
  approval_id: string
  references: string[]
}

export const fundKeys = {
  state: ['fund-state'] as const,
  config: ['fund-config'] as const,
  signals: ['fund-signals'] as const,
  risk: ['fund-risk'] as const,
  operations: ['fund-operations'] as const,
  performance: ['fund-performance'] as const,
  screened: ['fund-screened'] as const,
  approvals: ['approvals'] as const,
  audit: ['audit'] as const,
}

export const useFundState = () =>
  useQuery({ queryKey: fundKeys.state, refetchInterval: 20_000, queryFn: () => getJson<FundState>('/fund/state') })

export const useFundRisk = () =>
  useQuery({ queryKey: fundKeys.risk, queryFn: () => getJson<{ risk: RiskAssessment }>('/fund/risk') })

export const useFundOperations = () =>
  useQuery({ queryKey: fundKeys.operations, queryFn: () => getJson<Record<string, never> & {
    book: {
      cash: number; realised_pnl: number; commission_paid: number; slippage_paid: number
      simulated: boolean; venues: string[]
      positions: { symbol: string; quantity: number; average_price: number; realised_pnl: number }[]
      open_orders: { order_id: string; symbol: string; side: string; quantity: number; status: string }[]
    }
    reconciliation: { orders: number; fills: number; reconciled: boolean; discrepancies: unknown[] }
    orders: { order_id: string; symbol: string; side: string; quantity: number; status: string; filled_quantity: number; average_price: number | null; venue: string; accepted_at: string }[]
    fills: { fill_id: string; order_id: string; symbol: string; side: string; quantity: number; price: number; commission: number; slippage: number; filled_at: string; venue: string; simulated: boolean; basis: string }[]
    audit_summary: Record<string, number>
    execution_mode: string
    limitations: string[]
  }>('/fund/operations') })

export const useFundPerformance = () =>
  useQuery({ queryKey: fundKeys.performance, queryFn: () => getJson<Record<string, unknown>>('/fund/performance') })

export const useFundSignals = () =>
  useQuery({
    queryKey: fundKeys.signals,
    queryFn: () => getJson<{
      signals: { strategy_id: string; symbol: string; expected_return: number; confidence: number; verdict: string | null; run_id: string }[]
      limitations: string[]
    }>('/fund/signals'),
  })

export const useScreenedOrders = () =>
  useQuery({ queryKey: fundKeys.screened, queryFn: () => getJson<ScreenedOrder[]>('/fund/orders/screened') })

export const useApprovals = () =>
  useQuery({
    queryKey: fundKeys.approvals,
    refetchInterval: 20_000,
    queryFn: () => getJson<{ pending: Approval[]; history: Approval[] }>('/approvals'),
  })

export const useAudit = (limit = 120) =>
  useQuery({
    queryKey: [...fundKeys.audit, limit],
    refetchInterval: 15_000,
    queryFn: () => getJson<{ entries: AuditEntry[]; summary: Record<string, number> }>(`/audit?limit=${limit}`),
  })

/** Construct, prepare, screen, submit — each its own call, in that order.
 *
 * Not one "rebalance" mutation. The loop's whole point is that a proposal, a
 * screening and a submission are separate decisions with a person or a policy
 * between them, and collapsing them into a single button would put the gate
 * inside a step nobody can decline.
 */
export function useFundActions() {
  const client = useQueryClient()
  const after = () => {
    void client.invalidateQueries({ queryKey: fundKeys.state })
    void client.invalidateQueries({ queryKey: fundKeys.risk })
    void client.invalidateQueries({ queryKey: fundKeys.operations })
    void client.invalidateQueries({ queryKey: fundKeys.screened })
    void client.invalidateQueries({ queryKey: fundKeys.audit })
    void client.invalidateQueries({ queryKey: fundKeys.approvals })
  }
  return {
    construct: useMutation({
      mutationFn: () => postJson<{ portfolio: PortfolioProposal }>('/fund/portfolio', {}),
      onSuccess: after,
    }),
    screen: useMutation({
      mutationFn: (portfolio_id: string) =>
        postJson<{ screened: ScreenedOrder[]; cleared: string[]; blocked: string[] }>(
          '/fund/orders/screen',
          { portfolio_id },
        ),
      onSuccess: after,
    }),
    submit: useMutation({
      mutationFn: (order_ids: string[]) =>
        postJson<{ results: { order_id: string; accepted: boolean; reason?: string }[] }>(
          '/fund/orders/submit',
          { order_ids },
        ),
      onSuccess: after,
    }),
  }
}

export function useApprovalDecision() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: string; decision: 'approve' | 'reject'; note?: string }) =>
      postJson<Approval>(`/approvals/${id}/${decision}`, { note: note ?? '' }),
    onSuccess: () => client.invalidateQueries(),
  })
}
