import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, configure, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { App } from './App'

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))
configure({ asyncUtilTimeout: 12000 })

const summary = { strategy_count: 2, backtest_count: 3, template_count: 3, families: ['breakout'], strategies_path: 'F:/AlgoForge/strategies', data_gate: 'SYNTHETIC' }
const activity = [{ ts: '2026-09-01T20:11:04+00:00', stage: 'BACKTEST', level: 'pass', message: 'finished — 63 trades', ref: null }]
const engine = { running: false, started_at: null, cycles: 0, created: 0, backtested: 0, judged: 0, passed: 0, rejected: 0, skipped_by_memory: 0, compute_saved: 0, validation_passed: 0, holdout_passed: 0, lineages_retired: 0, engine_errors: 0, last_error: null, current_stage: 'idle', config: { dataset: 'nq_1m_16y', cycle_seconds: 6, max_strategies: 60, max_bars: 30000 } }
const datasetList = [{ key: 'nq_1m_16y', label: 'NQ · 1m · 16 years', symbol: 'NQ', interval: '1m', provider: 'databento-batch', authority: 'TRUTH', is_real: true, is_imported: true, available: true, cost_note: 'imported', loaded: true, bar_count: 4824845, span_years: 16.17, bars_per_year: 298444, ranges: [{ years: 1, label: '1 year', bars: 298444, available: true }] }]
const research = {
  families: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', variant_count: 0, tested_count: 0, positive_share: null, median_expectancy: null, best_expectancy: null, validation_oos_count: 0, holdout_count: 0, required_data: 'OHLCV_BARS' }],
  matrix: { markets: ['MNQ.CME'], rows: [{ key: 'orb', name: 'Opening range breakout', cells: [{ market: 'MNQ.CME', status: 'NOT_TESTED', expectancy: null, evidence_tier: null }] }] },
  catalog: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', status: 'RUNNABLE', runnable: true, required_data: ['OHLCV_BARS'], minimum_timeframe: '1m', description: 'test', missing_capability: null, template_key: 'orb' }],
}
const missions = { missions: [], current: null, running: false, actions: [], recent_actions: [], roles: ['research'], max_steps: 12 }
const storage = { root: 'F:/Obsidian Vaults/AlgoForge-Vault', repo: 'F:/AlgoForge', pointer: 'F:/AlgoForge/config/storage.json', vault_mode: true, is_obsidian_vault: true, notes: 'F:/Obsidian Vaults/AlgoForge-Vault/10 AlgoForge', store: 'F:/Obsidian Vaults/AlgoForge-Vault/10 AlgoForge/.store', exists: true, writable: true, note_bytes: 4096, counts: { strategies: 2, strategy_notes: 2, paper_notes: 1, backtest_notes: 1, verdict_notes: 0, family_notes: 12, mission_notes: 0, custom_templates: 0 }, mirror: { enabled: true, notes_written: 4, notes_skipped: 0, last_error: null, notes_root: 'x' }, folders: [], stays_in_repo: [] }
const loop = { enabled: true, running: true, in_flight: false, interval_minutes: 30, topics: [], topic_index: 0, cycles: 2, sources_found: 3, downstream_tasks: 1, last_started: null, last_finished: null, next_run: null, last_error: null, scope: 'nq', authority: 'UNREVIEWED' }

globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  const data = url.endsWith('/health') ? { status: 'ok', data_gate: 'REAL', engine_running: false }
    : url.endsWith('/engine') ? engine : url.endsWith('/datasets') ? datasetList
    : url.endsWith('/summary') ? summary : url.endsWith('/research/overview') ? research
    : url.endsWith('/research-loop') ? loop : url.includes('/activity') ? activity
    : url.includes('/experiments') ? [] : url.endsWith('/strategies') ? []
    : url.endsWith('/templates') ? [] : url.endsWith('/prop/rules') ? []
    : url.endsWith('/families') ? [] : url.endsWith('/research/sources') ? []
    : url.endsWith('/missions') ? missions : url.endsWith('/storage') ? storage
    : url.includes('/memory') ? { scope: 'nq', counts: {}, total: 0, constraints: [] }
    : url.endsWith('/runs') ? [] : {}
  return { ok: true, text: async () => JSON.stringify({ data }) } as unknown as Response
})

afterEach(() => { cleanup(); window.history.replaceState(null, '', '#overview') })
const renderApp = () => { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); render(<QueryClientProvider client={client}><App /></QueryClientProvider>) }
const open = async (name: string) => { fireEvent.click(await screen.findByRole('link', { name })); await new Promise(resolve => setTimeout(resolve, 0)) }

test('overview leads with research state, not a hero statement', async () => {
  renderApp()
  // The counters and the candidate ranking are the page. A marketing headline
  // used to occupy this space and answered none of the questions below.
  expect(await screen.findByText(/active mission/i)).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /strongest candidates/i })).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /research activity/i })).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: /start engine/i })).toBeInTheDocument()
  expect(screen.getAllByText(/paper only/i).length).toBeGreaterThan(0)
})

test('overview separates never-measured strategies from measured ones', async () => {
  renderApp(); await screen.findByText(/active mission/i)
  // "Never run" and "In sample" are different claims from "Out of sample";
  // collapsing them into one count is the error the judge exists to prevent.
  for (const label of [/^holdout$/i, /^out of sample$/i, /^in sample$/i, /^never run$/i]) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }
})

test('navigation is grouped around research rather than fixture pages', async () => {
  renderApp(); await screen.findByText(/active mission/i)
  for (const group of ['Research', 'Validation', 'Research Memory', 'Data', 'Autonomous', 'System']) expect(screen.getByRole('heading', { name: group })).toBeInTheDocument()
  for (const gone of ['Verdict', 'Regimes', 'Risk & Monte Carlo', 'Evolution']) expect(screen.queryByRole('link', { name: gone })).not.toBeInTheDocument()
})

test('primary records have stable hash navigation', async () => {
  renderApp(); await screen.findByText(/active mission/i)
  // Anchored on the record panels themselves rather than on a page headline,
  // so the assertion survives copy changes and fails on a broken route.
  for (const [link, panel] of [['Runs', /run ledger/i], ['Memory', /search constraints/i], ['Data Health', /dataset registry/i]] as const) {
    await open(link)
    expect(await screen.findByRole('heading', { name: panel })).toBeInTheDocument()
  }
})

test('command palette opens with Ctrl K and indexes routes', async () => {
  renderApp(); await screen.findByText(/active mission/i)
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  expect(await screen.findByRole('dialog', { name: /navigate algoforge/i })).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText(/go to a view/i), { target: { value: 'Agent Command' } })
  expect(screen.getByRole('button', { name: /Agent Command.*Autonomous/i })).toBeInTheDocument()
})

test('strategies opens on the catalogue, not on one arbitrary record', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Strategies')
  // The library is four hundred records in practice. Landing on whichever one
  // sorted first hid the rest behind a detail pane.
  expect(await screen.findByLabelText(/filter strategies/i)).toBeInTheDocument()
  expect(await screen.findByText(/no strategies yet/i)).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /new from template/i })).toBeInTheDocument()
})

test('prop simulation opens on the cross-strategy matrix', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Prop Simulation')
  expect(await screen.findByRole('button', { name: /run the matrix/i })).toBeInTheDocument()
})

test('research library distinguishes untested cells from zero performance', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Research Library')
  expect(await screen.findByText(/no empty-cell imputation/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /forge matrix/i })); expect(await screen.findByText('NOT TESTED')).toBeInTheDocument()
})

test('event drawer renders committed activity', async () => {
  renderApp(); await screen.findByText(/active mission/i)
  fireEvent.click(screen.getByRole('button', { name: /open event drawer/i })); expect((await screen.findAllByText(/finished — 63 trades/)).length).toBeGreaterThan(0)
})

test('pipeline names every evidence stage', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Engine Pipeline')
  expect(await screen.findByText(/where a number comes from/i)).toBeInTheDocument()
  for (const stage of ['Sources', 'Catalogue', 'Candidates', 'Measurement', 'Judgement', 'Survival']) expect(screen.getByRole('heading', { name: stage, level: 3 })).toBeInTheDocument()
})

test('missions refuse an empty objective', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Missions')
  expect(await screen.findByLabelText(/what should the team do/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /plan and run/i })).toBeDisabled()
})

test('evidence and experiments explain honest empty states', async () => {
  renderApp(); await screen.findByText(/active mission/i); await open('Evidence')
  expect(await screen.findByText(/NO STRATEGIES/i)).toBeInTheDocument()
  await open('Experiments'); expect(await screen.findByText(/NO EXPERIMENTS/i)).toBeInTheDocument()
})
