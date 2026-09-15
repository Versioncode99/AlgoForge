import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, configure, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { App } from './App'

/* The shell, exercised through the interface a person uses.
 *
 * The fixtures below are the *shape* the API returns, not a convenience: the
 * product manifest drives the rail, so a test that invented its own navigation
 * would pass while the real one was empty.
 *
 * There is no mode to set up any more. The shell opens on the product, and a
 * test says where it is by setting the hash — which is also how a person gets
 * there. Everything the mode-scoped tests used to assert about *reachability*
 * is still asserted; what is gone is the state before the product.
 */

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))
configure({ asyncUtilTimeout: 12000 })

const tab = (id: string, label: string, detail = 'detail', advanced = false) => ({
  tab: id, label, detail, panel_kinds: [], advanced,
})
const dest = (
  route: string,
  label: string,
  group: string,
  tabs: ReturnType<typeof tab>[] = [],
) => ({
  route, label, detail: `${label} detail`, group, panel_kinds: [], tabs,
  default_tab: tabs.length ? tabs[0].tab : '',
})

/* The manifest, as `/navigation` serves it. Deliberately the real shape and the
 * real routes: a fixture that invented its own would let the rail pass here and
 * be empty in the product. */
const NAVIGATION = {
  groups: ['Workspace', 'Research', 'Trading', 'System'],
  destinations: [
    dest('home', 'Home', 'Workspace', [tab('summary', 'Summary'), tab('workspace', 'Panels')]),
    dest('chat', 'Chat', 'Workspace'),
    dest('research', 'Research', 'Research', [
      tab('workbench', 'Workbench'), tab('findings', 'Findings'),
      tab('experiments', 'Experiments'), tab('validation', 'Validation'),
      tab('evidence', 'Evidence'),
    ]),
    dest('campaigns', 'Campaigns', 'Research', [
      tab('all', 'Campaigns'), tab('control', 'Control'), tab('lineage', 'Lineage'),
      tab('memory', 'Memory'), tab('pipeline', 'Pipeline', 'd', true),
      tab('automation', 'Automation', 'd', true),
    ]),
    dest('strategies', 'Strategies', 'Research', [
      tab('library', 'Library'), tab('runs', 'Runs'), tab('trades', 'Trades'),
    ]),
    dest('markets', 'Markets', 'Research', [tab('charts', 'Charts'), tab('data', 'Data')]),
    dest('trading', 'Trading', 'Trading', [
      tab('overview', 'Overview'), tab('book', 'Positions & Orders'),
      tab('portfolio', 'Portfolio'), tab('risk', 'Risk'), tab('gate', 'Pre-trade gate'),
      tab('execution', 'Execution'), tab('performance', 'Performance'),
      tab('operations', 'Operations', 'd', true),
    ]),
    dest('propdesk', 'Prop Desk', 'Trading', [
      tab('accounts', 'Accounts'), tab('status', 'Status'), tab('rules', 'Rules'),
      tab('drawdown', 'Drawdown'),
      tab('daily', 'Daily loss'), tab('target', 'Profit target'),
      tab('allocation', 'Allocation'), tab('copy', 'Copy'), tab('risk', 'Risk controls'),
      tab('limits', 'Limits'), tab('news', 'News'), tab('simulation', 'Simulation'),
      tab('activity', 'Activity', 'd', true), tab('ai', 'AI control', 'd', true),
    ]),
    dest('settings', 'Settings', 'System', [
      tab('general', 'General'), tab('models', 'Models'), tab('data', 'Data'),
      tab('connections', 'Connections'), tab('permissions', 'Permissions'),
      tab('approvals', 'Approvals'), tab('audit', 'Audit', 'd', true),
      tab('diagnostics', 'Diagnostics', 'd', true),
    ]),
  ],
  legacy_routes: {
    overview: 'home?tab=summary',
    assistant: 'chat',
    evidence: 'research?tab=evidence',
    gate: 'trading?tab=gate',
    positions: 'trading?tab=book',
    book: 'trading?tab=overview',
    approvals: 'settings?tab=approvals',
    actions: 'settings?tab=diagnostics',
    orchestrator: 'settings?tab=diagnostics',
    activity: 'settings?tab=diagnostics',
    desk: 'propdesk?tab=accounts',
    account: 'propdesk?tab=status',
    charts: 'markets?tab=charts',
  },
}

let authority = {
  profile: {
    unattended_work: true, unattended_execution: false,
    label: 'Unattended research',
    summary: 'The assistant also starts and stops campaigns on its own.',
    equivalent_mode: 'ai', equivalent_stance: 'human_in_the_loop',
  },
  available: [
    {
      unattended_work: false, unattended_execution: false, label: 'Assisted',
      summary: 'The assistant researches, backtests, validates and explains.',
      equivalent_mode: 'normal', equivalent_stance: null,
    },
    {
      unattended_work: true, unattended_execution: false, label: 'Unattended research',
      summary: 'The assistant also starts and stops campaigns on its own.',
      equivalent_mode: 'ai', equivalent_stance: 'human_in_the_loop',
    },
    {
      unattended_work: true, unattended_execution: true, label: 'Unattended execution',
      summary: 'The assistant runs the whole loop unattended, submission included.',
      equivalent_mode: 'ai', equivalent_stance: 'autonomous',
    },
  ],
  policy: {
    mode: 'ai', stance: 'human_in_the_loop', summary: 'AI assists.',
    always_denied_to_ai: ['protected controls', 'high-risk actions'],
  },
}

let propStatus: unknown = { account: null, assessment: null, reason: 'no state has been recorded' }
let fundState: unknown = null
let screened: unknown[] = []
let posted: string[] = []

const stage = (route: string, label: string, status: string, summary: string) => ({
  stage: route, label, purpose: 'p', route, produces: 'x', status, summary, count: null, detail: '',
})

const RISK_OK = {
  limits_name: 'default', within_limits: true, enabled: true,
  measures: [
    { key: 'gross', label: 'Gross exposure', value: 0.4, ceiling: 2, method: 'sum of absolute weights', note: '' },
    { key: 'var_95', label: 'VaR 95% (1 day)', value: null, ceiling: 0.03, method: '', note: 'no covariance estimate was supplied' },
  ],
  breaches: [], exposures: {}, by_strategy: {}, limitations: [],
}

const summary = { strategy_count: 2, backtest_count: 3, template_count: 3, families: ['breakout'], strategies_path: 'F:/AlgoForge/strategies', data_gate: 'SYNTHETIC' }
const activity = [{ ts: '2026-09-01T20:11:04+00:00', stage: 'BACKTEST', level: 'pass', message: 'finished — 63 trades', ref: null }]
const engine = { running: false, started_at: null, cycles: 0, created: 0, backtested: 0, judged: 0, passed: 0, rejected: 0, skipped_by_memory: 0, compute_saved: 0, validation_passed: 0, holdout_passed: 0, lineages_retired: 0, engine_errors: 0, last_error: null, current_stage: 'idle', config: { dataset: 'nq_1m_16y', cycle_seconds: 6, max_strategies: 60, max_bars: 30000 } }
const research = {
  families: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', variant_count: 0, tested_count: 0, positive_share: null, median_expectancy: null, best_expectancy: null, validation_oos_count: 0, holdout_count: 0, required_data: 'OHLCV_BARS' }],
  matrix: { markets: ['MNQ.CME'], rows: [{ key: 'orb', name: 'Opening range breakout', cells: [{ market: 'MNQ.CME', status: 'NOT_TESTED', expectancy: null, evidence_tier: null }] }] },
  catalog: [{ key: 'orb', name: 'Opening range breakout', family: 'breakout', status: 'RUNNABLE', runnable: true, required_data: ['OHLCV_BARS'], minimum_timeframe: '1m', description: 'test', missing_capability: null, template_key: 'orb' }],
}
const loop = { enabled: true, running: true, in_flight: false, interval_minutes: 30, topics: [], topic_index: 0, cycles: 2, sources_found: 3, downstream_tasks: 1, last_started: null, last_finished: null, next_run: null, last_error: null, scope: 'nq', authority: 'UNREVIEWED' }
const memory = { scope: 'nq', counts: {}, total: 0, constraints: [] }
const missions = { missions: [], current: null, running: false, actions: [], recent_actions: [], roles: ['research'], max_steps: 12 }
const operations = {
  book: {
    cash: 1_000_000, realised_pnl: 0, commission_paid: 0, slippage_paid: 0,
    simulated: true, venues: [], positions: [], open_orders: [],
  },
  reconciliation: { orders: 0, fills: 0, reconciled: true, discrepancies: [] },
  orders: [], fills: [], audit_summary: {}, execution_mode: 'PAPER',
  limitations: ['Execution is simulated locally.'],
}

globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (init?.method === 'POST' || init?.method === 'PUT') posted.push(url)

  const data = url.includes('/navigation') ? NAVIGATION
    : url.endsWith('/authority') ? authority
    : url.endsWith('/modes/permissions') ? {
        mode: 'ai', stance: 'human_in_the_loop',
        actions: [
          { action: 'backtest_strategy', summary: 'Run a backtest.', mutating: true, risk: 'safe', protected: false, ruling: 'allow', reason: 'preparatory' },
          { action: 'submit_orders', summary: 'Route cleared orders.', mutating: true, risk: 'safe', protected: false, ruling: 'require_approval', reason: 'reaches the book' },
          { action: 'set_fund_config', summary: 'Replace the fund configuration.', mutating: true, risk: 'safe', protected: true, ruling: 'deny', reason: 'changes a protected control' },
        ],
      }
    : url.endsWith('/fund/state') ? fundState
    : url.endsWith('/fund/risk') ? { risk: RISK_OK }
    : url.endsWith('/fund/operations') ? operations
    : url.endsWith('/fund/orders/screened') ? screened
    : url.includes('/approvals') ? { pending: [], history: [] }
    : url.includes('/audit') ? { entries: [], summary: {} }
    : url.includes('/prop/accounts/status') ? propStatus
    : url.endsWith('/prop/accounts') ? { accounts: propStatus && (propStatus as { account?: unknown }).account ? [(propStatus as { account: unknown }).account] : [], selected: null }
    : url.endsWith('/engine') ? engine
    : url.endsWith('/research-loop') ? loop
    : url.includes('/memory') ? memory
    : url.endsWith('/research/overview') ? research
    : url.endsWith('/missions') ? missions
    : url.endsWith('/health') ? { status: 'ok', data_gate: 'REAL', engine_running: false }
    : url.endsWith('/summary') ? summary
    : url.includes('/activity') ? activity
    : url.endsWith('/strategies') ? []
    : url.includes('/experiments') ? []
    : url.endsWith('/runs') ? []
    : url.endsWith('/templates') ? []
    : url.endsWith('/datasets') ? []
    : {}
  return { ok: true, text: async () => JSON.stringify({ data }) } as unknown as Response
})

beforeEach(() => {
  propStatus = { account: null, assessment: null, reason: 'no state has been recorded' }
  fundState = null
  screened = []
  posted = []
  window.history.replaceState(null, '', '#')
})
afterEach(cleanup)

const renderApp = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
}
const open = async (name: string | RegExp) => {
  fireEvent.click(await screen.findByRole('link', { name }))
  await new Promise((resolve) => setTimeout(resolve, 0))
}

// ── mode selection ───────────────────────────────────────────────────────────

describe('the front door', () => {
  test('opens on the product rather than on a choice about the product', async () => {
    renderApp()
    // No chooser, no "which kind of user are you" — the first screen is Home.
    expect(await screen.findByRole('link', { name: 'Home' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /choose your workspace/i })).not.toBeInTheDocument()
    for (const gone of ['Normal', 'Prop Firm', 'AI']) {
      expect(screen.queryByRole('heading', { name: gone, level: 2 })).not.toBeInTheDocument()
    }
    expect(window.location.hash).toBe('#home?tab=summary')
  })

  test('the primary destinations fit on one screen and name the work', async () => {
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    const rail = screen.getByRole('navigation', { name: /sections/i })
    const rows = within(rail).getAllByRole('link')
    // Nine, from sixty-two across three rails. The number is the requirement.
    expect(rows.length).toBe(9)
    for (const name of ['Home', 'Chat', 'Research', 'Campaigns', 'Strategies', 'Trading', 'Prop Desk', 'Settings']) {
      expect(within(rail).getByRole('link', { name })).toBeInTheDocument()
    }
  })

  test('campaigns is a primary destination, not something buried in AI', async () => {
    renderApp()
    const rail = await screen.findByRole('navigation', { name: /sections/i })
    expect(within(rail).getByRole('link', { name: 'Campaigns' })).toBeInTheDocument()
  })

  test('AI is presented as a conversation, never as a mode or a console', async () => {
    renderApp()
    const rail = await screen.findByRole('navigation', { name: /sections/i })
    expect(within(rail).getByRole('link', { name: 'Chat' })).toBeInTheDocument()
    for (const banned of [/AI mode/i, /AI workspace/i, /control cent/i, /console/i, /orchestrator/i]) {
      expect(within(rail).queryByText(banned)).not.toBeInTheDocument()
    }
  })

  test('the machinery is reachable but is not a primary destination', async () => {
    renderApp()
    const rail = await screen.findByRole('navigation', { name: /sections/i })
    for (const banned of ['Orchestrator', 'Actions', 'Activity', 'Approvals', 'Audit Log', 'Agents']) {
      expect(within(rail).queryByRole('link', { name: banned })).not.toBeInTheDocument()
    }
    // Reachable, though: the link a bookmark still carries lands on it.
    window.history.replaceState(null, '', '#orchestrator')
    cleanup()
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    expect(window.location.hash).toBe('#settings?tab=diagnostics')
  })

  test('the safety-critical facts stay in the header and the rest leave it', async () => {
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    // In the header specifically. Home says it too, which is fine — it is the
    // one fact worth repeating.
    const header = screen.getByRole('banner', { name: /workspace status/i })
    expect(within(header).getByText(/paper only/i)).toBeInTheDocument()
    // Eleven header facts became two. Strategy, tested and OOS counts belong on
    // the screens that own them, not beside the paper-only boundary.
    for (const gone of [/^STRATEGIES$/, /^TESTED$/, /^OOS$/, /recent events/i]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument()
    }
  })
})

// ── the tabbed destinations ──────────────────────────────────────────────────

describe('destinations and their tabs', () => {
  test('a destination shows its own views as tabs rather than as rail rows', async () => {
    window.history.replaceState(null, '', '#research')
    renderApp()
    const tabs = await screen.findByRole('navigation', { name: /research views/i })
    for (const name of ['Workbench', 'Findings', 'Experiments', 'Validation', 'Evidence']) {
      expect(within(tabs).getByRole('link', { name })).toBeInTheDocument()
    }
    // And none of them is competing for a row in the rail.
    const rail = screen.getByRole('navigation', { name: /sections/i })
    expect(within(rail).queryByRole('link', { name: 'Validation' })).not.toBeInTheDocument()
  })

  test('a link written for the old rail lands on the screen it named', async () => {
    window.history.replaceState(null, '', '#evidence')
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    // Translated, not dropped, and the hash is rewritten so the reader can see
    // where they actually are.
    expect(window.location.hash).toBe('#research?tab=evidence')
  })

  test('a link naming a tab that does not exist lands on the default, visibly', async () => {
    window.history.replaceState(null, '', '#research?tab=nonsense')
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    expect(window.location.hash).toBe('#research?tab=workbench')
  })

  test('a deep link keeps the target it carried', async () => {
    window.history.replaceState(null, '', '#strategies?strategy=abc')
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    expect(window.location.hash).toBe('#strategies?tab=library&strategy=abc')
  })

  test('an unknown route lands on Home rather than on a blank screen', async () => {
    window.history.replaceState(null, '', '#no_such_screen')
    renderApp()
    await screen.findByRole('link', { name: 'Home' })
    expect(window.location.hash).toBe('#home?tab=summary')
  })
})

// ── what an assistant may do ─────────────────────────────────────────────────

describe('permissions, where the mode chooser\'s second job went', () => {
  test('the settings screen offers the three configurations and names the current one', async () => {
    window.history.replaceState(null, '', '#settings?tab=permissions')
    renderApp()
    expect(await screen.findByRole('heading', { name: /what an assistant may do/i })).toBeInTheDocument()
    for (const label of ['Assisted', 'Unattended research', 'Unattended execution']) {
      expect(screen.getByRole('radio', { name: new RegExp(label, 'i') })).toBeInTheDocument()
    }
    expect(screen.getByRole('radio', { name: /unattended research/i })).toBeChecked()
  })

  test('changing it calls the API rather than only changing the screen', async () => {
    window.history.replaceState(null, '', '#settings?tab=permissions')
    renderApp()
    await screen.findByRole('heading', { name: /what an assistant may do/i })
    fireEvent.click(screen.getByRole('radio', { name: /^assisted/i }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    // Authority is server state: it decides what an unattended run may do, so a
    // choice that lived only in the browser would be no choice at all.
    expect(posted.some((url) => url.endsWith('/authority'))).toBe(true)
  })

  test('what is never permitted is stated on every setting', async () => {
    window.history.replaceState(null, '', '#settings?tab=permissions')
    renderApp()
    await screen.findByRole('heading', { name: /what an assistant may do/i })
    expect(screen.getByText(/protected controls/i)).toBeInTheDocument()
    expect(screen.getByText(/high-risk actions/i)).toBeInTheDocument()
  })
})

// ── prop firm ────────────────────────────────────────────────────────────────

describe('the prop desk', () => {
  test('refuses to invent an account, and says why there is no default', async () => {
    window.history.replaceState(null, '', '#propdesk?tab=status')
    renderApp()
    expect(await screen.findByText(/no account is configured/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing here is a copy of any firm/i)).toBeInTheDocument()
  })

  test('shows every rule with its buffer, and keeps unmeasured apart from passing', async () => {
    window.history.replaceState(null, '', '#propdesk?tab=status')
    const account = {
      account_id: 'a1', name: '50k Evaluation', rules_id: 'r1',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      rules: { name: '50k Evaluation', phase: 'CHALLENGE', trail_mode: 'end_of_day' },
    }
    propStatus = {
      account,
      state_source: 'entered by the operator',
      assessment: {
        rules_id: 'r1', rules_name: '50k Evaluation', as_of: '2026-03-04T15:00:00Z',
        level: 'warning', can_trade: true, balance: 50_800, equity: 50_600, loss_floor: 49_000,
        statuses: [
          { key: 'max_drawdown', label: 'Maximum loss', level: 'ok', observed: 50_600, limit: 49_000, buffer: 1600, headroom: 0.8, detail: 'floor 49,000.00' },
          { key: 'daily_loss', label: 'Daily loss limit', level: 'warning', observed: 800, limit: 1000, buffer: 200, headroom: 0.2, detail: '200.00 of today’s allowance remains' },
          { key: 'session', label: 'Trading session', level: 'not_assessed', observed: null, limit: null, buffer: null, headroom: null, detail: 'no session restriction is configured' },
        ],
        breaches: [], limitations: [],
      },
    }
    renderApp()
    await screen.findByText('Maximum loss')
    expect(screen.getByText('Trading permitted')).toBeInTheDocument()
    expect(screen.getByText(/200\.00 of today’s allowance remains/)).toBeInTheDocument()
    // A rule nobody could check must not read as a rule that passed. The judge
    // makes the same distinction with INCONCLUSIVE, and it uses the same words.
    expect(screen.getAllByText('NOT MEASURED').length).toBeGreaterThan(0)
    expect(screen.getByText(/asserts nothing about what any named firm/i)).toBeInTheDocument()
  })
})

// ── ai mode ──────────────────────────────────────────────────────────────────

describe('the action registry, under diagnostics', () => {
  test('shows the permission policy per action, including what is denied outright', async () => {
    // Reclassified, not removed. The registry is machinery — somebody inspects
    // it when a run went wrong — so it is a tab under Settings rather than a
    // row in the rail beside Strategies.
    window.history.replaceState(null, '', '#settings?tab=diagnostics')
    renderApp()
    fireEvent.click(await screen.findByRole('tab', { name: /actions/i }))
    expect(await screen.findByText('backtest_strategy')).toBeInTheDocument()
    expect(screen.getByText('ALLOWED')).toBeInTheDocument()
    expect(screen.getByText('NEEDS YOU')).toBeInTheDocument()
    expect(screen.getByText('DENIED')).toBeInTheDocument()
    expect(screen.getByText('PROTECTED')).toBeInTheDocument()
    expect(screen.getByText(/cannot grant itself the permissions that stance carries/i)).toBeInTheDocument()
  })
})

// ── the book loop ────────────────────────────────────────────────────────────
//
// These surfaces were Hedge Fund mode's. The mode was a product category rather
// than a capability and it went; the engines behind these screens did not, and
// they are reached from Normal now. That is what these tests are for: the
// removal has to be a relabelling, not a quiet feature deletion.

describe('the book loop', () => {
  const openFund = (risk = RISK_OK) => {
    fundState = {
      nav: 1_000_000, cash: 1_000_000, capital: 1_000_000, realised_pnl: 0,
      gross_exposure: 0, net_exposure: 0, leverage: 0, risk,
      execution_mode: 'PAPER', simulated: true, stance: 'human_in_the_loop',
      stages: [
        stage('data', 'Data', 'idle', 'no universe configured'),
        stage('validation', 'Validation', 'unknown', 'nothing judged yet'),
        stage('risk', 'Risk', risk.enabled ? 'ready' : 'halted', risk.enabled ? 'within limits' : 'kill switch engaged'),
      ],
      limitations: ['NAV marks open positions at their average fill price.'],
    }
  }

  test('the command centre leads with the book and the state of every stage', async () => {
    openFund()
    window.history.replaceState(null, '', '#trading?tab=overview')
    renderApp()
    expect(await screen.findByText('NAV')).toBeInTheDocument()
    expect(screen.getByText('WITHIN LIMITS')).toBeInTheDocument()
    // A stage nobody measured is drawn as unmeasured, not as quiet.
    expect(screen.getByText('nothing judged yet')).toBeInTheDocument()
    expect(screen.getByText(/no universe configured/)).toBeInTheDocument()
  })

  test('a disabled limit set is reported as a kill switch, not as a setting', async () => {
    openFund({ ...RISK_OK, enabled: false, within_limits: false })
    window.history.replaceState(null, '', '#trading?tab=overview')
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/kill switch/i)
    expect(alert).toHaveTextContent(/nothing an assistant does can change that/i)
  })

  test('the gate shows the full ladder and the reasons a blocked order failed', async () => {
    openFund()
    window.history.replaceState(null, '', '#trading?tab=gate')
    screened = [
      {
        order: { order_id: 'o1', symbol: 'CL', side: 'buy', quantity: 1, order_type: 'market', strategy_id: 's1', reference_price: 78 },
        decision: {
          order_id: 'o1', symbol: 'CL', decision: 'block', evaluated_at: '2026-03-04T15:00:00Z',
          clearance: null,
          reasons: ['CL is on the restricted list: under internal review'],
          checks: [
            { check: 'kill_switch', status: 'pass', detail: 'active' },
            { check: 'restricted_list', status: 'fail', detail: 'CL is on the restricted list' },
            { check: 'borrow', status: 'not_applicable', detail: 'borrow availability has never been established' },
          ],
        },
      },
    ]
    renderApp()
    expect(await screen.findByText('BLOCKED')).toBeInTheDocument()
    expect(screen.getByText(/on the restricted list: under internal review/)).toBeInTheDocument()
    // "Not established" is not a pass. The gate blocks on it, and the ladder has
    // to show that it did rather than showing a neutral row.
    expect(screen.getByText('NOT ESTABLISHED')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit 0 cleared order/i })).toBeDisabled()
  })

  test('approvals explain what happens when a person says yes', async () => {
    // The approval queue went to AI, not to Normal: it exists to hold what an
    // AI actor proposed, so it belongs beside the actor it is holding.
    openFund()
    window.history.replaceState(null, '', '#settings?tab=approvals')
    renderApp()
    expect(await screen.findByText(/nothing is waiting/i)).toBeInTheDocument()
    expect(screen.getByText(/approving runs it as you, now/i)).toBeInTheDocument()
  })
})

// ── the book, shared across modes ────────────────────────────────────────────

test('an open position is never drawn as flat when nothing marked it', async () => {
  window.history.replaceState(null, '', '#trading?tab=book')
  operations.book.positions = [
    { symbol: 'NQ', quantity: 1, average_price: 20_000.25, realised_pnl: 0 },
  ] as never
  renderApp()
  expect(await screen.findByText('NQ')).toBeInTheDocument()
  // Unrealised P&L has no feed behind it in this build. A zero here would read
  // as "flat", which is a different and false claim.
  expect(screen.getByText('not marked')).toBeInTheDocument()
  expect(screen.getByText('SIMULATED')).toBeInTheDocument()
  operations.book.positions = [] as never
})

test('home still leads with research state', async () => {
  renderApp()
  expect(await screen.findByText(/active mission/i)).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /strongest candidates/i })).toBeInTheDocument()
  // "Never run" and "In sample" are different claims from "Out of sample";
  // collapsing them into one count is the error the judge exists to prevent.
  for (const label of [/^holdout$/i, /^out of sample$/i, /^in sample$/i, /^never run$/i]) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }
})

test('the event ledger is still rendered, under diagnostics', async () => {
  /* The footer drawer is gone: it carried the latest event and a count of
   * recent ones on every screen in the product, which is a permanent strip
   * nobody was looking for. The ledger itself did not move an inch. */
  window.history.replaceState(null, '', '#settings?tab=diagnostics')
  renderApp()
  expect(await screen.findByRole('tab', { name: /event stream/i })).toBeInTheDocument()
  expect((await screen.findAllByText(/finished — 63 trades/)).length).toBeGreaterThan(0)
})

test('the command palette indexes every destination and every tab', async () => {
  renderApp()
  await screen.findByRole('link', { name: 'Home' })
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  expect(await screen.findByRole('dialog', { name: /search and run commands/i })).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText(/run a command/i), { target: { value: 'Positions' } })
  expect(screen.getByRole('button', { name: /Trading · Positions & Orders/i })).toBeInTheDocument()
})

test('a palette row says what it does, not which key does it', async () => {
  // "open" and "stop" are very different things to be one keystroke away from,
  // and the row used to end in a bare ↵ for both.
  renderApp()
  await screen.findByRole('link', { name: 'Home' })
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  await screen.findByRole('dialog', { name: /search and run commands/i })
  fireEvent.change(screen.getByPlaceholderText(/run a command/i), { target: { value: 'Positions' } })
  expect(screen.getByRole('button', { name: /Trading · Positions & Orders.*open/i })).toBeInTheDocument()
})

test('strategies still opens on the catalogue rather than one record', async () => {
  window.history.replaceState(null, '', '#strategies')
  renderApp()
  expect(await screen.findByLabelText(/filter strategies/i)).toBeInTheDocument()
  expect(await screen.findByText(/no strategies yet/i)).toBeInTheDocument()
})

test('evidence still explains an honest empty state', async () => {
  window.history.replaceState(null, '', '#research?tab=evidence')
  renderApp()
  expect(await screen.findByText(/NO STRATEGIES/i)).toBeInTheDocument()
})
