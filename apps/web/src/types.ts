export type Run = {run_id: string; tier: string; labels: string[]; created_at: string}
export type Gate = {gate: string; name: string; status: string; finding: string}
export type Verdict = {verdict_id: string; decision: string; grade: string; dimensions: Record<string, number>; metrics: Record<string, number>; gates: Gate[]; labels: string[]}
export type Analysis = {verdict: Verdict; regimes: {name: string; trade_count: number; net_pnl: number; confidence: string}[]; risk: {equity_paths: number[][]; median_path: number[]; p05_path: number[]; p95_path: number[]; path_count: number; terminal_median: number; loss_probability: number; var_95: number; cvar_95: number; warnings: string[]}}
export type Rule = {rule_id: string; display_name: string; provider: string; phase: string; starting_balance: number; profit_target: number; maximum_loss: number; verified: boolean}
export type PropSimulation = {rule: Rule; pass_rate: number; interval_low: number; interval_high: number; pass_count: number; fail_count: number; timeout_count: number; path_count: number; mean_payout: number; equity_paths: number[][]; labels: string[]}
export type Debate = {roles: {role_id: string; can_read_holdout: boolean}[]; claims: {role_id: string; stance: string; statement: string; confidence: number}[]; dissent_present: boolean; numeric_verdict_locked: boolean}
export type Evolution = {automatic_live_changes: boolean; paper_only: boolean; release_count: number; candidate: {repository: string; lane: string; proposed_features: string[]}}
