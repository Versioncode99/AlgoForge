import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, configure, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { App } from './App'

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))
// Nine lazily-loaded sections; the tab walk mounts every one of them in a
// single test, and the last chunks land well past the 5s default.
configure({ asyncUtilTimeout: 12000 })

const summary = {
  strategy_count: 2, backtest_count: 3, template_count: 3,
  families: ['breakout'], strategies_path: 'F:/AlgoForge/strategies', data_gate: 'SYNTHETIC',
}
const activity = [{ ts: '2026-09-01T20:11:04+00:00', stage: 'BACKTEST', level: 'pass', message: 'finished — 63 trades', ref: null }]
const engine = {
  running: false, started_at: null, cycles: 0, created: 0, backtested: 0, judged: 0,
  passed: 0, rejected: 0, skipped_by_memory: 0, compute_saved: 0,
  last_error: null, current_stage: 'idle',
  config: { dataset: 'nq_1m_16y', cycle_seconds: 6, max_strategies: 60, max_bars: 30000 },
}
const datasetList = [
  {
    key: 'nq_1m_16y', label: 'NQ · 1m · 16 years', symbol: 'NQ', interval: '1m',
    provider: 'databento-batch', authority: 'TRUTH', is_real: true, is_imported: true,
    available: true, cost_note: 'imported', loaded: true, bar_count: 4824845,
    span_years: 16.17, bars_per_year: 298444,
    ranges: [
      { years: 1, label: '1 year', bars: 298444, available: true },
      { years: 3, label: '3 years', bars: 895332, available: true },
      { years: 16, label: 'Max', bars: 4775104, available: true },
    ],
  },
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

const missions = {
  missions: [], current: null, running: false,
  actions: [], recent_actions: [], roles: ['research'], max_steps: 12,
}
const storage = {
  root: 'F:/Obsidian Vaults/AlgoForge-Vault', repo: 'F:/AlgoForge',
  pointer: 'F:/AlgoForge/config/storage.json',
  vault_mode: true, is_obsidian_vault: true,
  notes: 'F:/Obsidian Vaults/AlgoForge-Vault/10 AlgoForge',
  store: 'F:/Obsidian Vaults/AlgoForge-Vault/10 AlgoForge/.store',
  exists: true, writable: true, note_bytes: 4096,
  counts: {
    strategies: 2, strategy_notes: 2, paper_notes: 1, backtest_notes: 1,
    verdict_notes: 0, family_notes: 12, mission_notes: 0, custom_templates: 0,
  },
  mirror: { enabled: true, notes_written: 4, notes_skipped: 0, last_error: null, notes_root: 'x' },
  folders: [], stays_in_repo: [],
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
    : url.endsWith('/prop/rules') ? []
    : url.endsWith('/families') ? []
    : url.endsWith('/research/sources') ? []
    : url.endsWith('/missions') ? missions
    : url.endsWith('/storage') ? storage
    : {}
  return { ok: true, text: async () => JSON.stringify({ data }) } as unknown as Response
})

afterEach(cleanup)

const renderApp = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}

test('overview is the engine control centre and carries the paper-only label', async () => {
  renderApp()
  expect(await screen.findByText(/autonomous engine/i)).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: /start engine/i })).toBeInTheDocument()
  expect(screen.getByText(/paper only · fills are modelled/i)).toBeInTheDocument()
})

test('the fixture-driven sections are gone', async () => {
  // Verdict, Regimes, Risk & Monte Carlo, Agents and Evolution all rendered one
  // seeded run, so nothing on them could be acted on. Their absence is the
  // feature; this test stops them coming back by accident.
  renderApp()
  await screen.findByText(/autonomous engine/i)
  for (const gone of ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Agents', 'Evolution']) {
    expect(screen.queryByRole('button', { name: gone })).not.toBeInTheDocument()
  }
})

test('every remaining tab reaches a section that renders', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  for (const tab of [
    'Pipeline', 'Orchestrator', 'Research Lab', 'Strategies', 'Prop Firm', 'Console', 'Settings',
  ]) {
    fireEvent.click(screen.getByRole('button', { name: tab }))
    expect(await screen.findByRole('heading', { name: tab, level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/paper only · fills are modelled/i)).toBeInTheDocument()
  }
})

test('strategies offers a data set and a range before running anything', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Strategies' }))
  expect(await screen.findByText(/select a strategy/i)).toBeInTheDocument()
})

test('prop firm opens on the matrix, not a single-strategy form', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Prop Firm' }))
  expect(await screen.findByRole('button', { name: /run the matrix/i })).toBeInTheDocument()
  expect(screen.getByText(/simulates every strategy that has a long enough backtest/i)).toBeInTheDocument()
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

test('the pipeline names every stage a candidate has to pass', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Pipeline' }))
  expect(await screen.findByText(/where a number comes from/i)).toBeInTheDocument()
  for (const stage of ['Sources', 'Catalogue', 'Candidates', 'Measurement', 'Judgement', 'Survival']) {
    expect(screen.getByRole('heading', { name: stage, level: 3 })).toBeInTheDocument()
  }
})

test('the orchestrator will not launch a mission from an empty objective', async () => {
  renderApp()
  await screen.findByText(/autonomous engine/i)
  fireEvent.click(screen.getByRole('button', { name: 'Orchestrator' }))
  expect(await screen.findByLabelText(/what should the team do/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /plan and run/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /plan it/i })).toBeDisabled()
})
