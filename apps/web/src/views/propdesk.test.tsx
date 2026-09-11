import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { PropDeskView } from './PropDesk'

/* The Prop Desk screens, exercised against the shapes the API actually returns.
 *
 * These tests are about honesty rather than layout. Three claims the product
 * makes are only true if the screen renders them, and each one has a test:
 *
 * - a provider with no live connector says so, on the screen, in words;
 * - a firm permission nobody recorded does not look like permission;
 * - a refused order shows the rung that refused it and why.
 */

const PROVIDERS = {
  providers: [
    {
      provider: 'simulated',
      display_name: 'AlgoForge Simulator',
      what_it_is: "This application's own execution simulator.",
      auth_method: 'none',
      environments: ['simulation'],
      account_key_fields: ['account_id'],
      bracket_model: 'emulated',
      rate_limit_per_minute: null,
      session_seconds: null,
      live_connector_implemented: true,
      documentation_url: '',
      evidence: 'local',
      limitations: ['Fills are modelled, not calibrated against any venue.'],
    },
    {
      provider: 'rithmic',
      display_name: 'Rithmic (R | Protocol)',
      what_it_is: 'Order-routing and account infrastructure.',
      auth_method: 'username_password',
      environments: ['demo', 'live'],
      account_key_fields: ['system', 'fcm_id', 'ib_id', 'account_id'],
      bracket_model: 'native_oso',
      rate_limit_per_minute: null,
      session_seconds: null,
      live_connector_implemented: false,
      documentation_url: 'https://www.rithmic.com/apis',
      evidence: 'dossier',
      limitations: ['Password custody: there is no delegated auth.'],
    },
  ],
  platforms: [
    {
      platform: 'ninjatrader_desktop',
      display_name: 'NinjaTrader Desktop',
      provider: null,
      signal_source_only: false,
      note: 'A charting and order-entry front end. It holds only local Sim accounts.',
      evidence: 'dossier',
    },
  ],
  data_feeds: [],
  live_connectors_implemented: ['simulated'],
  adapters: {
    simulated: 'executes against a local simulator',
    rithmic: 'declared; refuses every command, no live connector in this build',
  },
  required_work: {
    rithmic: {
      engineering: ['Implement R | Protocol over WebSocket.'],
      external: ['Pass conformance before it may reach production systems.'],
      credentials: ['A Rithmic system, username and password per connection.'],
      open_questions: [],
    },
  },
}

const CONNECTIONS = {
  connections: [
    {
      connection_id: 'conn-1',
      provider: 'simulated',
      environment: 'simulation',
      label: 'Simulator',
      state: 'live',
      platform: null,
      last_error: '',
      health: {
        provider: 'simulated',
        connection_id: 'conn-1',
        state: 'live',
        heartbeat_age_seconds: 0,
        session_expires_in_seconds: null,
        consecutive_failures: 0,
        rate_budget_remaining: 1,
        queued_commands: 0,
        last_error: '',
      },
    },
  ],
  accounts: [
    {
      account_uid: 'acct-1',
      canonical: 'simulated:simulation:sim-credential:F1',
      display_name: 'Follower one',
      account_type: 'simulation',
      connection_id: 'conn-1',
      balance: 50000,
      equity: null,
      prop_account_id: null,
      policy_id: null,
      capability: {
        order_types: [],
        max_contracts: null,
        can_trade: null,
        bracket_model: 'emulated',
      },
    },
  ],
}

const POLICIES = {
  policies: [
    {
      policy_id: 'pol-1',
      version: 'v1',
      firm_label: 'my firm',
      program_label: '50k',
      automation: 'unknown',
      copy_in: 'allowed',
      copy_out: 'unknown',
      algorithmic_allocation: 'blocked',
      cross_account_hedging: 'blocked',
      third_party_copy: 'blocked',
      order_origin: 'unspecified',
      permitted_products: ['MNQ'],
      prohibited_products: [],
      source_note: '',
    },
  ],
  questions: [
    { key: 'automation', question: 'Does this programme permit automated orders?' },
    { key: 'copy_in', question: 'May trades be copied onto this account?' },
  ],
  values: ['allowed', 'blocked', 'unknown', 'requires_confirmation'],
  note: 'AlgoForge ships no firm rules.',
}

const ACTIVITY = {
  decisions: [
    {
      decision_id: 'dec-1',
      cleared: false,
      dispatched: false,
      blocking_stages: ['compatibility'],
      stages: [
        { stage: 'account_binding', passed: true, unknown: false, detail: 'Follower one' },
        { stage: 'connection', passed: true, unknown: false, detail: 'the connection is live' },
        {
          stage: 'compatibility',
          passed: false,
          unknown: true,
          detail:
            'no programme policy has been evaluated for this account. An unrecorded rule is not permission.',
        },
      ],
      reasons: ['compatibility: an unrecorded rule is not permission'],
      at: '2026-09-11T15:30:00+00:00',
      intent: {
        account_uid: 'acct-1',
        kind: 'place',
        symbol: 'MNQ',
        side: 'buy',
        quantity: 2,
        reason: 'copy',
      },
      acknowledgement: null,
    },
  ],
  reconciliations: [],
  allocation_changes: [],
  health: [],
  counts: { connections: 1 },
}

const NEWS = {
  events: [],
  policy: {
    enabled: false,
    minutes_before: 2,
    minutes_after: 2,
    minimum_impact: 'high',
    action: 'block_new',
  },
  assessment: {
    restricted: false,
    action: '',
    events: [],
    next_event: null,
    minutes_to_next: null,
    gaps: ['no news policy is enabled'],
  },
  availability: [
    {
      provider: 'fred',
      available: false,
      reason: 'FRED_API_KEY is not set.',
      requires: ['FRED_API_KEY'],
      attribution: 'This product uses the FRED® API but is not endorsed…',
    },
  ],
  sources: [
    {
      name: 'Forex Factory',
      status: 'unsuitable',
      detail: 'No official API. The site returns HTTP 403 to automated requests.',
      url: 'https://www.forexfactory.com/calendar',
    },
    {
      name: 'FRED release dates',
      status: 'implemented',
      detail: 'Official Federal Reserve Bank of St. Louis API.',
      url: 'https://fred.stlouisfed.org/',
    },
  ],
}

const ROUTES: Record<string, unknown> = {
  '/propdesk/providers': PROVIDERS,
  '/propdesk/connections': CONNECTIONS,
  '/propdesk/policies': POLICIES,
  '/propdesk/groups': { groups: [] },
  '/propdesk/allocation': { allocations: [], constraints: {}, history: [] },
  '/propdesk/activity?limit=200': ACTIVITY,
  '/propdesk/news?days=14': NEWS,
}

function draw(section: 'desk' | 'limits' | 'news' | 'desk_activity' | 'copy' | 'allocation') {
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

describe('the desk says what it cannot reach', () => {
  test('a provider with no live connector says so, and what it would take', async () => {
    draw('desk')
    expect(await screen.findByText('Rithmic (R | Protocol)')).toBeInTheDocument()
    expect(screen.getAllByText('No live connector').length).toBeGreaterThan(0)
    expect(screen.getByText('Connector implemented')).toBeInTheDocument()
    expect(
      screen.getByText('Pass conformance before it may reach production systems.'),
    ).toBeInTheDocument()
  })

  test('a platform is listed without a provider rather than beside one', async () => {
    draw('desk')
    expect(await screen.findByText('NinjaTrader Desktop')).toBeInTheDocument()
    expect(
      screen.getByText(/holds only local Sim accounts/),
    ).toBeInTheDocument()
  })

  test('an equity the provider has not reported is not drawn as zero', async () => {
    draw('desk')
    expect(await screen.findByText('Follower one')).toBeInTheDocument()
    expect(
      screen.getAllByText(/has not reported one/).length,
    ).toBeGreaterThan(0)
    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  test('an account with no rule set linked says it cannot be assessed', async () => {
    draw('desk')
    expect(await screen.findByText('Follower one')).toBeInTheDocument()
    expect(
      screen.getByText(/cannot be assessed or traded/),
    ).toBeInTheDocument()
  })
})

describe('a rule nobody recorded does not look like permission', () => {
  test('unknown renders as "Not recorded", visibly apart from "Allowed"', async () => {
    draw('limits')
    expect(await screen.findByText('my firm · 50k')).toBeInTheDocument()
    expect(screen.getByText('Allowed')).toBeInTheDocument()
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
  })
})

describe('a refusal is shown with the rung that refused it', () => {
  test('the ladder names every stage and the reason for the blocking one', async () => {
    draw('desk_activity')
    expect(await screen.findByText('Refused')).toBeInTheDocument()
    expect(screen.getByText('account binding')).toBeInTheDocument()
    expect(screen.getByText('compatibility')).toBeInTheDocument()
    expect(
      screen.getByText(/An unrecorded rule is not permission/),
    ).toBeInTheDocument()
  })
})

describe('the news research is on the screen rather than in a document', () => {
  test('Forex Factory is shown as unsuitable, with the reason', async () => {
    draw('news')
    expect(await screen.findByText('Forex Factory')).toBeInTheDocument()
    expect(screen.getByText('unsuitable')).toBeInTheDocument()
    expect(screen.getByText(/returns HTTP 403/)).toBeInTheDocument()
  })

  test('a calendar that could not be read says which key it needs', async () => {
    draw('news')
    expect(await screen.findByText(/FRED_API_KEY is not set/)).toBeInTheDocument()
  })
})
