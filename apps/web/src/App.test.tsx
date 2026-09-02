import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { App } from './App'

vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="chart" /> }))

const run = { run_id: 'run_1', tier: 'TRUTH_OOS', labels: ['SAMPLE_DATA'], created_at: '2026-09-01' }
const verdict = {
  verdict_id: 'v1', decision: 'PASS', grade: 'B',
  dimensions: { edge: 60, robustness: 70, risk: 80, sample: 20 },
  metrics: { net_pnl: 500, win_rate: 0.55, profit_factor: 1.4, max_drawdown: 200 },
  gates: [{ gate: 'G0', name: 'Data', status: 'PASS', finding: 'Passed' }], labels: [],
}
const analysis = {
  verdict,
  regimes: [{ name: 'TREND', trade_count: 9, net_pnl: 100, confidence: 'LOW' }],
  risk: {
    equity_paths: [[1, 2]], median_path: [1], p05_path: [0], p95_path: [2], path_count: 240,
    terminal_median: 500, loss_probability: 0.1, var_95: 200, cvar_95: 300, warnings: [],
  },
}
const summary = {
  strategy_count: 2, backtest_count: 3, template_count: 3,
  families: ['breakout'], strategies_path: 'F:/AlgoForge/strategies', data_gate: 'SYNTHETIC',
}
const activity = [{ ts: '2026-09-01T20:11:04+00:00', stage: 'BACKTEST', level: 'pass', message: 'finished — 63 trades', ref: null }]
const engine = {
  running: false, started_at: null, cycles: 0, created: 0, backtested: 0, judged: 0,
  passed: 0, rejected: 0, skipped_by_memory: 0, compute_saved: 0,
  last_error: null, current_stage: 'idle',
  config: { dataset: 'mnq_1m_3mo', cycle_seconds: 6, max_strategies: 60, max_bars: 30000 },
}
const datasetList = [
  { key: 'mnq_1m_3mo', label: 'MNQ · 1m · 3 months', symbol: 'MNQ', interval: '1m',
    provider: 'databento', authority: 'TRUTH', is_real: true, cost_note: '~$0.33',
    loaded: true, bar_count: 90029 },
]
const research = {
  families: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', variant_count: 0,
    tested_count: 0, positive_share: null, median_expectancy: null, best_expectancy: null,
    validation_oos_count: 0, holdout_count: 0, required_data: 'OHLCV_BARS' }],
  matrix: { markets: ['MNQ.CME'], rows: [{ key: 'orb', name: 'Opening range breakout', cells: [
    { market: 'MNQ.CME', status: 'NOT_TESTED', expectancy: null, evidence_tier: null },
  ] }] },
  catalog: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', status: 'RUNNABLE',
    runnable: true, required_data: ['OHLCV_BARS'], minimum_timeframe: '1m', description: 'test',
    missing_capability: null, template_key: 'orb' }],
}

globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  const data = url.endsWith('/health') ? { status: 'ok', data_gate: 'REAL', engine_running: false }
    : url.endsWith('/engine') ? engine
    : url.endsWith('/datasets') ? datasetList
    : url.endsWith('/summary') ? summary
    : url.endsWith('/research/overview') ? research
    : url.includes('/activity') ? activity
    : url.endsWith('/strategies') ? []
    : url.endsWith('/templates') ? []
    : url.endsWith('/runs') ? [run]
    : url.includes('/analysis/') ? analysis
    : url.endsWith('/prop/rules') ? []
    : url.includes('/agents/') ? { claims: [], roles: [], dissent_present: true, numeric_verdict_locked: true }
    : { automatic_live_changes: false, paper_only: true, release_count: 0, candidate: { repository: 'owner/repo', lane: 'CLEAN_ROOM', proposed_features: [] } }
  return { ok: true, text: async () => JSON.stringify({ data }) } as unknown as Response
})

// vitest runs without globals, so RTL's automatic cleanup never registers itself.
afterEach(cleanup)

const renderApp = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

test('overview is the engine control centre and carries the paper-only label', async () => {
  renderApp()
  expect(await screen.findByText(/autonomous engine/i)).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: /start engine/i })).toBeInTheDocument()
  expect(screen.getByText(/paper only · real data is not oos/i)).toBeInTheDocument()
})

test('navigates to the agent boundary', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Agents' }))
  expect(await screen.findByText(/agents explain; the judge decides/i)).toBeInTheDocument()
})

test('exposes the strategy library as a first-class section', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Strategies' }))
  expect(await screen.findByText(/the code this system runs/i)).toBeInTheDocument()
})

test('prop firm lets you pick a strategy rather than scoring a fixture', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Prop Firm' }))
  expect(await screen.findByText(/race the target against the loss boundary/i)).toBeInTheDocument()
})

test('research lab distinguishes untested cells from zero performance', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Research Lab' }))
  expect(await screen.findByText(/what has evidence/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /forge matrix/i }))
  expect(await screen.findByText('NOT TESTED')).toBeInTheDocument()
})

test('orchestrator log renders real recorded events', async () => {
  renderApp()
  expect(await screen.findByText(/finished — 63 trades/)).toBeInTheDocument()
})
