import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { PropDeskView } from './PropDesk'

/* The Risk and AI Management screens.
 *
 * Four claims the product makes, each true only if the screen renders it:
 *
 * - the appetite meter is shown against what it costs, not as a size dial;
 * - a consequential setting cannot be switched on before its disclosure is read;
 * - a driver that was not measured says so rather than reading as neutral;
 * - what AI may never do is listed with the control that stops it.
 */

const CATALOGUE = {
  modes: [
    { mode: 'manual', promise: 'I decide.', detail: 'You set the risk fraction.' },
    { mode: 'adaptive', promise: 'AlgoForge calculates.', detail: 'You set an appetite.' },
    {
      mode: 'ai_managed',
      promise: 'AlgoForge manages within my boundaries.',
      detail: 'An advisory layer may reduce the result.',
    },
  ],
  capabilities: [
    { capability: 'position_sizing', label: 'Position sizing', detail: 'Propose a smaller fraction.' },
    { capability: 'risk_increase', label: 'Risk increase', detail: 'Apply an increase. Off by default.' },
  ],
  prohibitions: [
    {
      statement: 'Override your maximum risk',
      enforced_by: 'forge.propdesk.risk.RiskBoundaries',
      detail: 'Every proposal is clamped to the boundaries you set.',
    },
    {
      statement: 'Bypass the pre-trade gate',
      enforced_by: 'forge.execution.gate.screen',
      detail: 'The gate is the last rung of the desk funnel.',
    },
  ],
  autonomy_levels: [
    { level: 'off', label: 'Off', detail: 'Nothing is deployed without you.' },
    { level: 'approval_required', label: 'Approval required', detail: 'It is put to you.' },
    { level: 'fully_autonomous', label: 'Fully autonomous', detail: 'You are not asked.' },
  ],
  mandatory_gates: [
    { kind: 'validation', name: 'Validated', question: 'has the judge returned PASS?' },
    { kind: 'pretrade', name: 'Pre-trade', question: 'did an order clear the gate?' },
  ],
  disclosures: [
    {
      key: 'ai_risk_management',
      title: 'AI Risk Management',
      body: [
        'AI Risk Management can automatically adjust position sizing and exposure within the limits you configure.',
        'AlgoForge does not guarantee profits or prevent losses.',
      ],
      acknowledgements: ['I understand how AI Risk Management operates.'],
      confirm_label: 'Enable AI Risk Management',
      cancel_label: 'Cancel',
      claims: [],
      version: 'abc123',
    },
    {
      key: 'autonomous_deployment',
      title: 'Autonomous Strategy Deployment',
      body: ['Autonomous deployment can result in financial losses.'],
      acknowledgements: [
        'I understand that autonomous deployment can result in financial losses.',
        'I have reviewed my deployment and risk limits.',
      ],
      confirm_label: 'Enable Autonomous Deployment',
      cancel_label: 'Cancel',
      claims: [],
      version: 'def456',
    },
  ],
  questions: [],
}

const BOUNDARIES = {
  minimum_fraction: 0.05,
  maximum_fraction: 0.25,
  max_step: 0.02,
  cooldown_minutes: 240,
  hysteresis: 0.005,
  max_daily_change: 0.05,
  max_contracts: 5,
  emergency_buffer_ratio: 0.35,
  boundaries_hash: 'hash0123456789',
}

const RISK = {
  accounts: [
    {
      account_uid: 'acct-1',
      settings: {
        account_uid: 'acct-1',
        mode: 'adaptive',
        appetite: 52,
        boundaries: BOUNDARIES,
        manual: null,
        ai_capabilities: [],
        disclosure_version: '',
        updated_at: '2026-09-11T15:30:00+00:00',
        updated_by: 'operator',
        promise: 'AlgoForge calculates.',
        detail: 'You set an appetite.',
        automated: true,
      },
      state: {
        account_uid: 'acct-1',
        current_fraction: 0.12,
        last_change_at: null,
        change_today: 0,
        change_day: '',
        last_direction: '',
      },
      last_proposal: {
        account_uid: 'acct-1',
        strategy_id: 's1',
        mode: 'adaptive',
        at: '2026-09-11T15:30:00+00:00',
        current_fraction: 0.12,
        proposed_fraction: 0.09,
        applied_fraction: 0.1,
        direction: 'decrease',
        drivers: [
          {
            kind: 'realised_volatility',
            measured: true,
            observed: '180.00 against a modelled 120.00 (150%)',
            effect: 0.67,
            detail: 'realised volatility is 50% above what the evidence modelled',
            label: 'Realised volatility',
            cuts: true,
          },
          {
            kind: 'correlation',
            measured: false,
            observed: '',
            effect: 1,
            detail: 'correlation across the strategies running for this owner has not been computed',
            label: 'Portfolio correlation',
            cuts: false,
          },
        ],
        band: {
          appetite: 52,
          quantile: 0.87,
          drawdown_per_contract: 460,
          target_fraction: 0.154,
          conservative_fraction: 0.05,
          aggressive_fraction: 0.25,
          contracts: 3,
          distribution: {
            observed_days: 200,
            paths: 2000,
            horizon_days: 21,
            p50: 220,
            p75: 310,
            p90: 420,
            p95: 460,
            p99: 610,
            worst: 980,
          },
        },
        unmeasurable: null,
        clamp: 'step',
        clamp_detail: 'the change was larger than your single-step limit',
        binding_driver: 'realised_volatility',
        emergency: false,
        contracts_before: 3,
        contracts_after: 2,
        changed: true,
        why: [
          'Realised volatility: realised volatility is 50% above what the evidence modelled (x0.67).',
          'Portfolio correlation: not measured — correlation across the strategies running for this owner has not been computed.',
          'Sizing moves from 3 to 2 contracts.',
        ],
      },
      autonomy: 'off',
    },
  ],
  catalogue: CATALOGUE,
}

const CONNECTIONS = {
  connections: [],
  accounts: [
    {
      account_uid: 'acct-1',
      canonical: 'simulated:simulation:sim:F1',
      display_name: 'Follower one',
      account_type: 'simulation',
      connection_id: 'conn-1',
      balance: 50000,
      equity: null,
      prop_account_id: null,
      policy_id: null,
      capability: { order_types: [], max_contracts: null, can_trade: null, bracket_model: 'emulated' },
    },
  ],
}

const AUDIT = {
  records: [
    {
      action: 'risk_adjusted',
      at: '2026-09-11T15:30:00+00:00',
      actor: 'system',
      actor_name: '',
      account_uid: 'acct-1',
      strategy_id: 's1',
      previous: { risk_fraction: 0.12 },
      current: { risk_fraction: 0.1 },
      risk_mode: 'adaptive',
      risk_fraction: 0.1,
      verdict: 'PASS',
      policy_state: 'allowed',
      automation_mode: 'off',
      reason: 'Realised volatility rose above the modelled level.',
      disclosure_version: '',
      approval: 'not_required',
      decision: 'decrease',
      execution_state: '',
      record_id: 'rec-1',
    },
  ],
  deployments: [
    {
      strategy_id: 's1',
      account_uid: 'acct-1',
      level: 'off',
      outcome: 'blocked',
      gates: [
        { kind: 'validation', passed: true, unknown: false, detail: 'verdict v-1', name: 'Validated', question: 'has the judge returned PASS?' },
        {
          kind: 'lifecycle',
          passed: false,
          unknown: false,
          detail: 'live execution is not available: this build contains no broker connector',
          name: 'Lifecycle',
          question: 'is the transition legal?',
        },
      ],
      at: '2026-09-11T15:30:00+00:00',
      cleared: false,
      reasons: ['Lifecycle: live execution is not available'],
      level_label: 'Off',
    },
  ],
  proposals: [],
}

const ROUTES: Record<string, unknown> = {
  '/propdesk/risk': RISK,
  '/propdesk/connections': CONNECTIONS,
  '/propdesk/audit?limit=200': AUDIT,
}

function draw(section: 'risk_management' | 'ai_management') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PropDeskView section={section} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = String(url).replace(/^.*\/api\/v1/, '')
      const body = ROUTES[path]
      if (body === undefined) {
        return new Response(JSON.stringify({ detail: { reason: `no fixture for ${path}` } }), {
          status: 404,
        })
      }
      return new Response(JSON.stringify({ data: body }), { status: 200 })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the three modes say what they promise', () => {
  test('each mode is offered with the sentence it makes', async () => {
    draw('risk_management')
    expect(await screen.findByText('I decide.')).toBeInTheDocument()
    expect(screen.getByText('AlgoForge calculates.')).toBeInTheDocument()
    expect(screen.getByText('AlgoForge manages within my boundaries.')).toBeInTheDocument()
  })
})

describe('the meter is shown against what it costs', () => {
  test('the appetite is drawn with the quantile and the contracts it buys', async () => {
    draw('risk_management')
    expect(await screen.findByText('52')).toBeInTheDocument()
    expect(screen.getByText(/87th-percentile bootstrapped/)).toBeInTheDocument()
    expect(screen.getByText(/3 contracts/)).toBeInTheDocument()
  })

  test('the operator ceiling is shown beside the current risk', async () => {
    draw('risk_management')
    expect(await screen.findByText('Your ceiling')).toBeInTheDocument()
    // Once as the headline stat, once in the boundaries table underneath.
    expect(screen.getAllByText('25.00%').length).toBe(2)
    expect(screen.getByText("of this account's buffer to its loss floor")).toBeInTheDocument()
  })
})

describe('an unmeasured driver does not read as neutral', () => {
  test('it is labelled "Not measured" rather than shown as x1.00', async () => {
    draw('risk_management')
    expect(await screen.findByText('Portfolio correlation')).toBeInTheDocument()
    expect(screen.getAllByText('Not measured').length).toBeGreaterThan(0)
  })

  test('the clamp that bound is explained with both numbers', async () => {
    draw('risk_management')
    expect(
      await screen.findByText(/Sizing moves from 3 to 2 contracts/),
    ).toBeInTheDocument()
  })
})

describe('a consequential setting needs its disclosure read first', () => {
  test('choosing AI risk management shows the warning before anything is saved', async () => {
    draw('risk_management')
    fireEvent.click(await screen.findByText('AlgoForge manages within my boundaries.'))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/does not guarantee profits or prevent losses/)).toBeInTheDocument()
  })

  test('the confirm button is disabled until every statement is ticked', async () => {
    draw('risk_management')
    fireEvent.click(await screen.findByText('AlgoForge manages within my boundaries.'))
    const confirm = await screen.findByRole('button', { name: 'Enable AI Risk Management' })
    expect(confirm).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(confirm).toBeEnabled()
  })
})

describe('AI management lists what it may never do', () => {
  test('each prohibition names the control that stops it', async () => {
    draw('ai_management')
    expect(await screen.findByText('Override your maximum risk')).toBeInTheDocument()
    expect(screen.getByText('forge.propdesk.risk.RiskBoundaries')).toBeInTheDocument()
    expect(screen.getByText('forge.execution.gate.screen')).toBeInTheDocument()
  })

  test('the mandatory control list is shown in full', async () => {
    draw('ai_management')
    // Twice: once in the published control list, once as the rung of the
    // deployment evaluation below it.
    expect((await screen.findAllByText('Validated')).length).toBe(2)
    expect(screen.getByText('has the judge returned PASS?')).toBeInTheDocument()
  })

  test('a blocked deployment shows the control that blocked it', async () => {
    draw('ai_management')
    expect(await screen.findByText('blocked')).toBeInTheDocument()
    expect(
      screen.getByText(/live execution is not available/),
    ).toBeInTheDocument()
  })

  test('the audit trail shows the reason recorded at the time', async () => {
    draw('ai_management')
    expect(
      await screen.findByText('Realised volatility rose above the modelled level.'),
    ).toBeInTheDocument()
  })
})
