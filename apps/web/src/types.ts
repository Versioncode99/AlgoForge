export type Run = {
  run_id: string; preregistration_id: string; preregistration_hash: string
  tier: string; source_hash: string; data_hash: string; cost_hash: string
  engine_version: string; status: string; labels: string[]; created_at: string
}
export type ResearchConstraint = {
  template: string; failure_class: string; reason: string; gate?: string | null
  strategy_id?: string | null; created_at?: string; [key: string]: unknown
}
export type ResearchMemoryPayload = {
  scope: string; counts: Record<string, number>; total: number
  constraints: ResearchConstraint[]
}
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
  calculation_version?: string
  backtest_id: string; net_pnl: number; trade_count: number; win_rate: number
  max_drawdown: number; parameters?: Record<string, number>; finished_at: string
  evidence_tier?: string; split_id?: string | null
}
export type StrategyListItem = StrategySpec & { backtest_count: number; latest: BacktestSummary | null }
export type WalkForward = {
  fold_count: number; in_sample_sharpe: number; out_of_sample_sharpe: number
  efficiency: number; positive_folds: number; consistency: number; degradation: number
}
export type PathSpread = {
  paths: number; mean_sharpe: number; median_sharpe: number
  sharpe_p05: number; sharpe_p95: number; positive_share: number; dispersion: number
}
export type ValidationEvidence = {
  evidence_id: string; trial_count: number
  probability_of_overfitting: number; cscv_splits: number
  walk_forward_efficiency: number; walk_forward_folds: number; walk_forward_consistency: number
  cpcv_paths: number; path_sharpe_p05: number; path_positive_share: number
  selection_stability: number; best_parameters: Record<string, number>
  grid: Record<string, number[]>
  walk_forward: WalkForward; paths: PathSpread
  trial_sharpes: number[]; cscv_logits: number[]
}
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
  evidence_tier: string; dataset_key: string | null; partition_name: string | null
  split_receipt?: ResearchSplitReceipt | null
  started_at: string; finished_at: string
}
export type PartitionReceipt = {
  name: string; evidence_tier: string; start_index: number; end_index: number
  bar_count: number; first_event_time: string; last_event_time: string; data_hash: string
}
export type ResearchSplitReceipt = {
  split_id: string; source_data_hash: string; source_bar_count: number; purge_bars: number
  development_fraction: number; validation_fraction: number
  development: PartitionReceipt; validation: PartitionReceipt; holdout: PartitionReceipt
}
export type StrategyDetail = {
  spec: StrategySpec; source: string; tests: string; code_hash: string; path: string
  backtests: BacktestSummary[]
}
export type TemplateInfo = {
  key: string; name: string; family: string; hypothesis: string
  falsifiable_prediction: string; parameters: ParameterSpec[]
  warmup_bars: number; line_count: number
  data_requirement: string; minimum_timeframe: string; research_status: string
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

/** The engine's actual condition, derived from worker heartbeats.
 *
 *  `running` used to be the only signal and it meant "threads exist". A search
 *  that had exhausted its campaign or was refusing every proposal reported
 *  RUNNING for as long as it was left alone. These are the states that can be
 *  told apart, and the interface renders this rather than the boolean. */
export type RuntimeState =
  | 'STARTING' | 'RUNNING' | 'PAUSED' | 'IDLE' | 'WAITING_FOR_WORK'
  | 'WAITING_FOR_DATA' | 'WAITING_FOR_AGENT' | 'BLOCKED' | 'EXHAUSTED'
  | 'STOPPING' | 'STOPPED' | 'ERROR' | 'RECOVERING'

export type WorkerHeartbeat = {
  worker_id: string; stage: string
  beat_at: string | null; progress_at: string | null; started_at: string | null
  seconds_since_beat: number; seconds_since_progress: number | null
  cycles: number; progressed: number; barren: number; errors: number
  paused: boolean; stale: boolean; dead: boolean
  claim: string | null; campaign_id: string | null; agent_id: string | null
  last_outcome: string | null; last_reason: string
}

export type RuntimeSnapshot = {
  state: RuntimeState; reason: string; code: string; remedy: string
  detail: Record<string, unknown>
  working: boolean; stalled: boolean
  started_at: string | null; last_progress_at: string | null
  seconds_without_progress: number | null
  workers: WorkerHeartbeat[]
  stale_workers: string[]; dead_workers: string[]
  outcome_counts: Record<string, number>
  recent_outcomes: { at: string | null; outcome: string; reason: string }[]
  blockers: Record<string, string>
}

/** The accounting that replaces the single `skipped_by_memory` figure.
 *
 *  `useful` declined an experiment that would otherwise have run. `wasted`
 *  refused nothing, because there was nothing to refuse — it is a symptom, and
 *  showing it as saved compute is what made the old number unusable. */
export type SkipCounts = {
  total: number; distinct: number
  useful: number; wasted: number; neutral: number
  by_kind: Record<string, number>
  by_level: Record<string, number>
}

export type EngineStatus = {
  stopping?: boolean
  running: boolean; started_at: string | null; cycles: number
  created: number; backtested: number; judged: number
  passed: number; rejected: number; skipped_by_memory: number; compute_saved: number
  validation_passed: number; holdout_passed: number; lineages_retired: number; engine_errors: number
  last_error: string | null; current_stage: string
  // One stage per search worker, keyed by worker index.
  worker_stages?: Record<string, string>
  pruned?: number; prop_tested?: number
  best_pass_rate?: number; best_strategy?: string | null; best_rule?: string | null
  // The split that replaces the single skip counter.
  skipped_duplicate?: number; skipped_by_region?: number; skipped_not_novel?: number
  skipped_no_work?: number; skipped_blocked?: number; skipped_exhausted?: number
  skipped_errors?: number; skipped_without_work?: number
  // Derived truth. `working` is only true when a worker progressed recently.
  runtime_state?: RuntimeState; runtime_reason?: string
  runtime_code?: string; runtime_remedy?: string
  working?: boolean
  runtime?: RuntimeSnapshot
  skips?: SkipCounts
  config: {
    dataset: string; cycle_seconds: number; max_strategies: number
    max_bars: number; workers?: number
  }
}
export type RangeOption = { years: number; label: string; bars: number; available: boolean }
export type DatasetInfo = {
  key: string; label: string; symbol: string; interval: string; provider: string
  authority: string; is_real: boolean; cost_note: string; loaded: boolean; bar_count: number
  is_imported: boolean; available: boolean
  span_years: number; bars_per_year: number; ranges: RangeOption[]
}
export type PropResult = {
  simulation_id: string; strategy_id: string; rule: Rule
  path_count: number; pass_count: number; fail_count: number; timeout_count: number
  pass_rate: number; interval_low: number; interval_high: number; mean_payout: number
  equity_paths: number[][]; trading_days: number; daily_pnl: number[]
  failure_reasons: Record<string, number>
  /* A sample of the paths, kept so individual accounts can be inspected. Every
     statistic on this object is computed over all of them; this is not. */
  sampled_terminal_balances: number[]; outcome_sample_size: number
  median_terminal: number
  risk_of_ruin: number
  boundary_race: {
    target_first_probability: number; loss_first_probability: number; timeout_probability: number
    target_days_p10: number | null; target_days_median: number | null; target_days_p90: number | null
    loss_days_p10: number | null; loss_days_median: number | null; loss_days_p90: number | null
  }
  target_reach_curve: { day: number; probability: number }[]
  terminal_histogram: { lower: number; upper: number; count: number }[]
  return_drawdown_map: { terminal_pnl: number; max_drawdown: number; outcome: string }[]
  tail_risk: {
    var_95: number; cvar_95: number; skewness: number; excess_kurtosis: number
    terminal_p05: number; terminal_median: number; terminal_p95: number
  }
  labels: string[]; interval_width: number; resample_ratio: number
}

export type ResearchOverview = {
  families: {
    key: string; name: string; family: string; variant_count: number; tested_count: number
    positive_share: number | null; median_expectancy: number | null; best_expectancy: number | null
    validation_oos_count: number; holdout_count: number; required_data: string
  }[]
  matrix: {
    markets: string[]
    rows: { key: string; name: string; cells: {
      market: string; status: string; expectancy: number | null; evidence_tier: string | null
    }[] }[]
  }
  catalog: {
    key: string; name: string; family: string; status: string; runnable: boolean
    required_data: string[]; minimum_timeframe: string; description: string
    missing_capability: string | null; template_key: string | null
  }[]
}

/* ── Settings & assistant ───────────────────────────────────────────────── */
export type ModelInfo = {
  id: string; label: string; tier: string; note: string
  // OpenCode Zen only: most of its catalogue is gated on account balance
  // rather than capability, so a model can be real, listed and still refuse.
  status?: 'verified' | 'needs_credit' | 'unavailable'
  origin?: 'china' | 'us' | 'other'
}
export type RoleInfo = { key: string; label: string; detail: string }
export type CredentialInfo = {
  key: string; label: string; detail: string; present: boolean; hint: string; source: string
}
export type BudgetSettings = {
  daily_usd_hard: number; daily_usd_soft: number
  monthly_usd_hard: number; per_session_usd: number; halt_on_breach: boolean
}
export type ProviderInfo = { id: string; label: string; detail: string }
export type SettingsPayload = {
  ai: {
    enabled: boolean; provider: string; base_url: string
    routing: Record<string, string>; budget: BudgetSettings
    gateway: {
      provider: string; connected: boolean; status_code: number | null; latency_ms: number
      model_count: number; credential_present: boolean; credential_source: string
      base_url: string; error: string | null
      // Present only under the `auto` provider: which one actually answered.
      selected?: string; fell_back?: boolean; note?: string
    }
  }
  default_dataset: string; engine_cycle_seconds: number
  engine_max_strategies: number; databento_max_cost_usd: number
  research_loop: { enabled: boolean; interval_minutes: number; topics: string[] }
  models: ModelInfo[]; roles: RoleInfo[]; credentials: CredentialInfo[]
  providers: ProviderInfo[]
}
export type ResearchLoopStatus = {
  enabled: boolean; running: boolean; in_flight: boolean; interval_minutes: number
  topics: string[]; topic_index: number; cycles: number; sources_found: number
  downstream_tasks: number; last_started: string | null; last_finished: string | null
  next_run: string | null; last_error: string | null; scope: string; authority: string
}
export type OracleInfo = {
  oracle_id: string; installed: boolean; version: string | null; ready: boolean
  supported_data_levels: string[]; configured_data_levels: string[]
  role: string; licence: string; limitations: string[]
}
export type AskResult = {
  answer: string; model: string; grounded: boolean; note?: string
  input_tokens?: number; output_tokens?: number
}

/* ── Background jobs ─────────────────────────────────────────────────────── */
export type Job = {
  job_id: string; kind: string; label: string
  status: 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED'
  total: number; done: number; note: string; fraction: number
  elapsed_seconds: number; eta_seconds: number | null
  error: string | null; created_at: number
  result?: unknown
}
export type BacktestJobResult = {
  result: BacktestResult
  meta: {
    dataset: string; provider: string; is_real: boolean; bar_count: number
    trade_count: number; evidence_tier: string
    development_backtest_id?: string; development_net_pnl?: number
    development_trade_count?: number
  }
}

/* ── Prop matrix ─────────────────────────────────────────────────────────── */
export type MatrixCell = {
  strategy_id: string; strategy_name: string
  rule_id: string; rule_name: string; provider: string; phase: string
  pass_rate: number; interval_low: number; interval_high: number
  risk_of_ruin: number; mean_payout: number; median_terminal: number
  var_95: number; cvar_95: number; trading_days: number; verified: boolean
}
export type MatrixSkip = {
  strategy_id: string; name: string; reason: string
  days_observed?: number; days_required?: number; detail?: string
}
export type PropMatrix = {
  cells: MatrixCell[]
  strategies: { strategy_id: string; name: string; trading_days: number }[]
  rules: { rule_id: string; display_name: string; provider: string; phase: string; verified: boolean }[]
  skipped: MatrixSkip[]
  paths: number
}

/* ── Storage, catalogue and orchestration ───────────────────────────────── */
export type StorageFolder = { name: string; path: string; kind: 'notes' | 'store' }
export type StoragePayload = {
  root: string; repo: string; pointer: string
  vault_mode: boolean; is_obsidian_vault: boolean
  notes: string; store: string; exists: boolean; writable: boolean
  note_bytes: number
  counts: {
    strategies: number; strategy_notes: number; paper_notes: number; backtest_notes: number
    verdict_notes: number; family_notes: number; mission_notes: number; custom_templates: number
  }
  mirror: {
    enabled: boolean; notes_written: number; notes_skipped: number
    last_error: string | null; notes_root: string
  }
  folders: StorageFolder[]
  stays_in_repo: { name: string; path: string; why: string }[]
}
export type StorageInspect = {
  path: string; exists: boolean; creatable: boolean; writable: boolean
  is_obsidian_vault: boolean; vault_mode: boolean; already_initialised: boolean
  existing_strategies: number; problems: string[]; usable: boolean
}

export type FamilyInfo = {
  key: string; label: string; description: string; mechanism: string
  data_requirements: string[]; origin: string; created_at: string; created_by: string
  templates: string[]; template_count: number
  runnable: boolean; blocked_by: string[]; status: 'RUNNABLE' | 'BLOCKED_DATA'
}
export type CatalogTemplate = TemplateInfo & {
  origin: 'builtin' | 'custom'; grid_points: number
}

export type ActionSchema = {
  name: string; description: string; mutating: boolean
  parameters: { type: string; properties: Record<string, Record<string, unknown>>; required: string[] }
}
export type MissionStep = {
  index: number; kind: 'action' | 'agent'; action: string | null; role: string | null
  task?: string; arguments?: Record<string, unknown>; label: string; why: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  summary: string; error: string | null; job_id: string | null
  started_at: number | null; finished_at: number | null
  result?: Record<string, unknown> | null
}
export type Mission = {
  id: string; objective: string
  status: 'planned' | 'running' | 'completed' | 'partial' | 'failed'
  plan_source: string; plan_model: string | null
  plan_rationale: string; plan_note: string | null
  stop_on_failure: boolean; started_at: number; finished_at: number | null
  steps: MissionStep[]; outcome: string | null; job_id: string | null
}
export type ActionRecord = {
  action: string; ok: boolean; arguments: Record<string, unknown>
  result?: Record<string, unknown>; error?: string; elapsed_seconds: number; at: number
}
export type MissionSnapshot = {
  missions: Mission[]; current: string | null; running: boolean
  actions: ActionSchema[]; recent_actions: ActionRecord[]
  roles: string[]; max_steps: number
}

/** One candidate the engine formed, with the line it came from.
 *  Shared between the Experiments view and the global search. */
export type ExperimentRecord = {
  id: string
  template: string
  parameters: Record<string, number>
  status?: string
  policy?: string
  family?: string
  parent_id?: string
  seed?: number
  failure_class?: string
  failure_gate?: string
  failure_reason?: string
  verdict_id?: string
  strategy_id?: string
  created_at?: string
  finished_at?: string
  development_sharpe?: number
}
