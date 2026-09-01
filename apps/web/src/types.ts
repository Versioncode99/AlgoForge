export type Run = { run_id: string; tier: string; labels: string[]; created_at: string }
export type Gate = { gate: string; name: string; status: string; finding: string; observed?: string | number; rule?: string }
export type Verdict = {
  verdict_id: string; decision: string; grade: string
  dimensions: Record<string, number>; metrics: Record<string, number>
  gates: Gate[]; labels: string[]
}
export type Analysis = {
  verdict: Verdict
  regimes: { name: string; trade_count: number; net_pnl: number; confidence: string }[]
  risk: {
    equity_paths: number[][]; median_path: number[]; p05_path: number[]; p95_path: number[]
    path_count: number; terminal_median: number; loss_probability: number
    var_95: number; cvar_95: number; warnings: string[]
  }
}
export type Rule = {
  rule_id: string; display_name: string; provider: string; phase: string
  starting_balance: number; profit_target: number; maximum_loss: number; verified: boolean
}
export type PropSimulation = {
  rule: Rule; pass_rate: number; interval_low: number; interval_high: number
  pass_count: number; fail_count: number; timeout_count: number; path_count: number
  mean_payout: number; equity_paths: number[][]; labels: string[]
}
export type Debate = {
  roles: { role_id: string; can_read_holdout: boolean }[]
  claims: { role_id: string; stance: string; statement: string; confidence: number }[]
  dissent_present: boolean; numeric_verdict_locked: boolean
}
export type Evolution = {
  automatic_live_changes: boolean; paper_only: boolean; release_count: number
  candidate: { repository: string; lane: string; proposed_features: string[] }
}

/* ── Strategy layer ─────────────────────────────────────────────────────── */
export type ParameterSpec = {
  name: string; default: number; low: number; high: number; step: number; description: string
}
export type StrategySpec = {
  strategy_id: string; name: string; lineage: string; family: string; market: string
  symbol: string; bar_spec: string; template: string
  hypothesis: string; falsifiable_prediction: string
  parameters: ParameterSpec[]; warmup_bars: number
  commission_per_side: number; slippage_ticks: number; tick_value: number
  created_at: string; created_by: string
}
export type BacktestSummary = {
  backtest_id: string; net_pnl: number; trade_count: number; win_rate: number
  max_drawdown: number; parameters?: Record<string, number>; finished_at: string
}
export type StrategyListItem = StrategySpec & { backtest_count: number; latest: BacktestSummary | null }
export type Trade = {
  trade_id: string; direction: number
  entry_decision_index: number; entry_index: number
  exit_decision_index: number; exit_index: number
  entry_time: string; exit_time: string; entry_price: number; exit_price: number
  gross_pnl: number; costs: number; net_pnl: number; bars_held: number; exit_reason: string
}
export type BacktestResult = {
  backtest_id: string; strategy_id: string; spec_hash: string; code_hash: string; data_hash: string
  parameters: Record<string, number>; bar_count: number; trades: Trade[]; equity: number[]
  net_pnl: number; gross_pnl: number; total_costs: number; win_rate: number
  max_drawdown: number; lookahead_clean: boolean; labels: string[]
  started_at: string; finished_at: string
}
export type StrategyDetail = {
  spec: StrategySpec; source: string; tests: string; code_hash: string; path: string
  backtests: BacktestSummary[]
}
export type TemplateInfo = {
  key: string; name: string; family: string; hypothesis: string
  falsifiable_prediction: string; parameters: ParameterSpec[]
  warmup_bars: number; line_count: number
}
export type SweepPoint = {
  value: number; net_pnl: number; trade_count: number; win_rate: number; max_drawdown: number
}
export type SweepResult = { parameter: ParameterSpec; points: SweepPoint[]; best: SweepPoint | null }
export type ActivityEvent = {
  ts: string; stage: string; level: 'info' | 'pass' | 'fail' | 'warn'; message: string; ref: string | null
}
export type Summary = {
  strategy_count: number; backtest_count: number; template_count: number
  families: string[]; strategies_path: string; data_gate: string
}

/* ── Engine, datasets, prop ─────────────────────────────────────────────── */
export type EngineStatus = {
  running: boolean; started_at: string | null; cycles: number
  created: number; backtested: number; judged: number
  passed: number; rejected: number; skipped_by_memory: number; compute_saved: number
  last_error: string | null; current_stage: string
  config: { dataset: string; cycle_seconds: number; max_strategies: number; max_bars: number }
}
export type DatasetInfo = {
  key: string; label: string; symbol: string; interval: string; provider: string
  authority: string; is_real: boolean; cost_note: string; loaded: boolean; bar_count: number
}
export type PropResult = {
  simulation_id: string; strategy_id: string; rule: Rule
  path_count: number; pass_count: number; fail_count: number; timeout_count: number
  pass_rate: number; interval_low: number; interval_high: number; mean_payout: number
  equity_paths: number[][]; trading_days: number; daily_pnl: number[]
  avg_days_to_pass: number | null; avg_days_to_fail: number | null
  failure_reasons: Record<string, number>; terminal_balances: number[]; median_terminal: number
}
