import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, configure, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { App } from './App'

/* The four-mode shell, exercised through the interface a person uses.
 *
 * The fixtures below are the *shape* the API returns, not a convenience: the
 * mode manifest drives the rail, so a test that invented its own navigation
 * would pass while the real one was empty. `session` is mutable so a test can
 * say which mode is open before rendering, which is the only piece of state the
 * whole shell hangs off.
 */

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))
configure({ asyncUtilTimeout: 12000 })

const section = (route: string, label: string, group: string, detail = 'detail') => ({
  route, label, group, detail, panel_kinds: [],
})

type Descriptor = {
  mode: string; name: string; tagline: string; purpose: string; workspace_template: string
  sections: { route: string; label: string; group: string; detail: string; panel_kinds: string[] }[]
  stances: string[]; default_stance: string | null; limitations: string[]
}

const NORMAL: Descriptor = {
  mode: 'normal',
  name: 'Normal',
  tagline: 'Your trading environment.',
  purpose: 'Trade, analyse and monitor markets without a firm’s constraints.',
  workspace_template: 'normal_desk',
  sections: [
    section('overview', 'Overview', 'Desk'),
    section('strategies', 'Strategies', 'Strategy'),
    section('evidence', 'Evidence', 'Strategy'),
    section('positions', 'Positions & Orders', 'Book'),
    section('settings', 'Settings', 'System'),
  ],
  stances: [],
  default_stance: null,
  limitations: ['Paper only. No live-order path exists anywhere in this application.'],
}

const PROP: Descriptor = {
  ...NORMAL,
  mode: 'prop_firm',
  name: 'Prop Firm',
  tagline: 'Trade within account constraints.',
  purpose: 'Operate a funded or evaluation account against its own rule set.',
  workspace_template: 'prop_desk',
  sections: [section('account', 'Account Status', 'Account'), section('rules', 'Rules', 'Account')],
  limitations: ['Rule sets are supplied by you.'],
}

const AI: Descriptor = {
  ...NORMAL,
  mode: 'ai',
  name: 'AI',
  tagline: 'Build, analyse and automate with AI.',
  purpose: 'Use AI across research, strategy work and automation.',
  workspace_template: 'ai_desk',
  sections: [section('actions', 'Actions', 'AI'), section('activity', 'Activity', 'AI')],
  limitations: ['AI reaches only the registered actions.'],
}

const HEDGE: Descriptor = {
  ...NORMAL,
  mode: 'hedge_fund',
  name: 'Hedge Fund',
  tagline: 'Research, construct, manage and execute quantitative portfolios.',
  purpose: 'An operating layer over the whole quantitative loop.',
  workspace_template: 'fund_command',
  sections: [
    section('fund', 'Fund Overview', 'Command'),
    section('gate', 'Pre-Trade Gate', 'Loop'),
    section('approvals', 'Approvals', 'Command'),
  ],
  stances: ['human_in_the_loop', 'autonomous'],
  default_stance: 'human_in_the_loop',
  limitations: ['Execution is simulated locally. No broker, OMS vendor or venue is connected.'],
}

const DESCRIPTORS: Record<string, Descriptor> = {
  normal: NORMAL, prop_firm: PROP, ai: AI, hedge_fund: HEDGE,
}

let session: { mode: string | null; stance: string | null; workspace_id: string | null } = {
  mode: null, stance: null, workspace_id: null,
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

  const mode = session.mode ?? 'ai'
  const descriptor = DESCRIPTORS[mode]

  const data = url.endsWith('/modes') ? { modes: [NORMAL, PROP, AI, HEDGE], loop: [] }
    : url.endsWith('/modes/session') ? {
        session, descriptor, policy_applies: session.mode !== null,
        policy: { mode, stance: session.stance, summary: 'AI assists.', always_denied_to_ai: [] },
      }
    : url.includes('/modes/') && url.endsWith('/enter') ? { session }
    : url.endsWith('/modes/leave') ? { session: { mode: null, stance: null, workspace_id: null } }
    : url.endsWith('/modes/permissions') ? {
        mode, stance: session.stance,
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
    : url.endsWith('/approvals') ? { pending: [], history: [] }
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
  session = { mode: null, stance: null, workspace_id: null }
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

describe('the opening screen', () => {
  test('offers all four workspaces rather than defaulting into one', async () => {
    renderApp()
    expect(await screen.findByRole('heading', { name: /choose your workspace/i })).toBeInTheDocument()
    for (const name of ['Normal', 'Prop Firm', 'AI', 'Hedge Fund']) {
      expect(await screen.findByRole('heading', { name, level: 2 })).toBeInTheDocument()
    }
    // Each panel says what it is for. Four names with no purpose would be a
    // pricing page, which is the thing this screen must not be.
    expect(screen.getByText(/trade within account constraints/i)).toBeInTheDocument()
    expect(screen.getByText(/research, construct, manage and execute/i)).toBeInTheDocument()
  })

  test('asks for the Hedge Fund stance before entering, not after', async () => {
    renderApp()
    await screen.findByRole('heading', { name: /choose your workspace/i })
    // The stance changes what an assistant may do unattended. Entering first and
    // asking later would mean the mode opens on a stance nobody picked.
    expect(await screen.findByRole('radiogroup', { name: /operating stance/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /human in the loop/i })).toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: /autonomous/i }))
    expect(screen.getByRole('radio', { name: /autonomous/i })).toBeChecked()
    expect(screen.getByText(/inside the risk engine, the pre-trade gate and the kill switch/i)).toBeInTheDocument()
  })

  test('states the paper-only boundary on the way in', async () => {
    renderApp()
    await screen.findByRole('heading', { name: /choose your workspace/i })
    expect(screen.getByText(/no broker, venue or order-routing vendor is connected/i)).toBeInTheDocument()
  })

  test('opening a mode calls the API rather than only changing the screen', async () => {
    renderApp()
    await screen.findByRole('heading', { name: /choose your workspace/i })
    fireEvent.click(await screen.findByRole('button', { name: /open hedge fund/i }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    // Mode is server state: each mode remembers its own layout, so entering one
    // has to be recorded somewhere a refresh can read it back.
    expect(posted.some((url) => url.endsWith('/modes/hedge_fund/enter'))).toBe(true)
  })
})

// ── the mode-scoped shell ────────────────────────────────────────────────────

describe('the shell inside a mode', () => {
  test('builds its navigation from the mode manifest', async () => {
    session = { mode: 'normal', stance: null, workspace_id: 'w1' }
    renderApp()
    expect(await screen.findByRole('link', { name: /strategies/i })).toBeInTheDocument()
    for (const group of ['Desk', 'Strategy', 'Book', 'System']) {
      expect(screen.getByRole('heading', { name: group })).toBeInTheDocument()
    }
    // Hedge Fund's sections must not leak into Normal's rail.
    expect(screen.queryByRole('link', { name: /pre-trade gate/i })).not.toBeInTheDocument()
  })

  test('names the open mode and offers a way back to the chooser', async () => {
    session = { mode: 'hedge_fund', stance: 'autonomous', workspace_id: 'w2' }
    fundState = {
      nav: 1_000_000, cash: 1_000_000, capital: 1_000_000, realised_pnl: 0,
      gross_exposure: 0, net_exposure: 0, leverage: 0, risk: RISK_OK,
      execution_mode: 'PAPER', simulated: true, stance: 'autonomous',
      stages: [stage('fund', 'Data', 'idle', 'no universe configured')],
      limitations: [],
    }
    renderApp()
    const badge = (await screen.findByText('Hedge Fund')).closest('.mode-badge')!
    // The autonomous stance is the one state where the machine acts unasked, so
    // it is named in the chrome rather than only on the screen that set it.
    expect(within(badge as HTMLElement).getByText(/autonomous/i)).toBeInTheDocument()

    // Switch now opens the workspace switcher rather than leaving the mode.
    // Changing mode changes what an assistant may do on your behalf, which is a
    // permissions decision and not a navigation one, so it lives inside the
    // switcher beside the arrangements rather than in the header where it read
    // as "switch screens".
    fireEvent.click(within(badge as HTMLElement).getByRole('button', { name: /switch/i }))
    const leave = await screen.findByRole('button', { name: /change operating mode/i })
    fireEvent.click(leave)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(posted.some((url) => url.endsWith('/modes/leave'))).toBe(true)
  })

  test('a hash that is not a route in this mode falls back rather than blanking', async () => {
    session = { mode: 'prop_firm', stance: null, workspace_id: 'w3' }
    window.history.replaceState(null, '', '#gate')
    renderApp()
    // "gate" is a Hedge Fund route. Prop Firm opens on its own first section.
    await screen.findByRole('link', { name: /account status/i })
    expect(window.location.hash).toBe('#account')
  })

  test('a hash chosen before a mode is entered is left alone', async () => {
    /* The opening screen is up, so the hash is not a route at all and the
     * session still carries whichever mode was last described. Correcting
     * against that manifest is how the front door's "start here" landed
     * somebody on the previous mode's first section every time. */
    session = { mode: null, stance: null, workspace_id: null }
    window.history.replaceState(null, '', '#desk')
    renderApp()
    await screen.findByRole('heading', { name: /choose your workspace/i })
    expect(window.location.hash).toBe('#desk')
  })
})

// ── prop firm ────────────────────────────────────────────────────────────────

describe('prop firm mode', () => {
  test('refuses to invent an account, and says why there is no default', async () => {
    session = { mode: 'prop_firm', stance: null, workspace_id: 'w3' }
    renderApp()
    expect(await screen.findByText(/no account is configured/i)).toBeInTheDocument()
    expect(screen.getByText(/nothing here is a copy of any firm/i)).toBeInTheDocument()
  })

  test('shows every rule with its buffer, and keeps unmeasured apart from passing', async () => {
    session = { mode: 'prop_firm', stance: null, workspace_id: 'w3' }
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

describe('ai mode', () => {
  test('shows the permission policy per action, including what is denied outright', async () => {
    session = { mode: 'ai', stance: null, workspace_id: 'w4' }
    window.history.replaceState(null, '', '#actions')
    renderApp()
    expect(await screen.findByText('backtest_strategy')).toBeInTheDocument()
    expect(screen.getByText('ALLOWED')).toBeInTheDocument()
    expect(screen.getByText('NEEDS YOU')).toBeInTheDocument()
    expect(screen.getByText('DENIED')).toBeInTheDocument()
    expect(screen.getByText('PROTECTED')).toBeInTheDocument()
    expect(screen.getByText(/cannot grant itself the permissions that stance carries/i)).toBeInTheDocument()
  })
})

// ── hedge fund ───────────────────────────────────────────────────────────────

describe('hedge fund mode', () => {
  const openFund = (risk = RISK_OK) => {
    session = { mode: 'hedge_fund', stance: 'human_in_the_loop', workspace_id: 'w5' }
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

  test('the command centre leads with the fund and the state of every stage', async () => {
    openFund()
    renderApp()
    expect(await screen.findByText('NAV')).toBeInTheDocument()
    expect(screen.getByText('WITHIN LIMITS')).toBeInTheDocument()
    // A stage nobody measured is drawn as unmeasured, not as quiet.
    expect(screen.getByText('nothing judged yet')).toBeInTheDocument()
    expect(screen.getByText(/no universe configured/)).toBeInTheDocument()
  })

  test('a disabled limit set is reported as a kill switch, not as a setting', async () => {
    openFund({ ...RISK_OK, enabled: false, within_limits: false })
    renderApp()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/kill switch/i)
    expect(alert).toHaveTextContent(/nothing an assistant does can change that/i)
  })

  test('the gate shows the full ladder and the reasons a blocked order failed', async () => {
    openFund()
    window.history.replaceState(null, '', '#gate')
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
    openFund()
    window.history.replaceState(null, '', '#approvals')
    renderApp()
    expect(await screen.findByText(/nothing is waiting/i)).toBeInTheDocument()
    expect(screen.getByText(/approving runs it as you, now/i)).toBeInTheDocument()
  })
})

// ── the book, shared across modes ────────────────────────────────────────────

test('an open position is never drawn as flat when nothing marked it', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  window.history.replaceState(null, '', '#positions')
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

test('the Normal overview still leads with research state', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  renderApp()
  expect(await screen.findByText(/active mission/i)).toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /strongest candidates/i })).toBeInTheDocument()
  // "Never run" and "In sample" are different claims from "Out of sample";
  // collapsing them into one count is the error the judge exists to prevent.
  for (const label of [/^holdout$/i, /^out of sample$/i, /^in sample$/i, /^never run$/i]) {
    expect(screen.getByText(label)).toBeInTheDocument()
  }
})

test('the event drawer still renders committed activity', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  renderApp()
  await screen.findByRole('link', { name: /strategies/i })
  fireEvent.click(screen.getByRole('button', { name: /open event drawer/i }))
  expect((await screen.findAllByText(/finished — 63 trades/)).length).toBeGreaterThan(0)
})

test('the command palette indexes the open mode’s sections', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  renderApp()
  await screen.findByRole('link', { name: /strategies/i })
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
  expect(await screen.findByRole('dialog', { name: /navigate algoforge/i })).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText(/go to a view/i), { target: { value: 'Positions' } })
  expect(screen.getByRole('button', { name: /Positions & Orders.*Book/i })).toBeInTheDocument()
})

test('strategies still opens on the catalogue rather than one record', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  renderApp()
  await open(/strategies/i)
  expect(await screen.findByLabelText(/filter strategies/i)).toBeInTheDocument()
  expect(await screen.findByText(/no strategies yet/i)).toBeInTheDocument()
})

test('evidence still explains an honest empty state', async () => {
  session = { mode: 'normal', stance: null, workspace_id: 'w1' }
  renderApp()
  await open(/evidence/i)
  expect(await screen.findByText(/NO STRATEGIES/i)).toBeInTheDocument()
})
