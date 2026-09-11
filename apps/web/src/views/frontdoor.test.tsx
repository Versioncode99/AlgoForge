import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { ModeSelect } from './ModeSelect'

/* The opening screen's two bands.
 *
 * Three claims, each true only if the screen renders it:
 *
 * - an intent shows its caveat before it takes you anywhere;
 * - the depth selector says plainly that nothing is removed by choosing one;
 * - an API answering with an unexpected shape does not take the screen down.
 */

const MODES = {
  modes: [
    {
      mode: 'normal',
      name: 'Normal',
      tagline: 'Your trading environment.',
      purpose: 'Trade, analyse and monitor markets.',
      workspace_template: 'normal_desk',
      sections: [{ route: 'strategies', label: 'Strategies', detail: '', group: 'Strategy', panel_kinds: [] }],
      stances: [],
      default_stance: null,
      limitations: ['Paper only.'],
    },
    {
      mode: 'prop_firm',
      name: 'Prop Firm',
      tagline: 'Trade within account constraints.',
      purpose: 'Operate a funded account against its rule set.',
      workspace_template: 'prop_desk',
      sections: [{ route: 'desk', label: 'Prop Desk', detail: '', group: 'Desk', panel_kinds: [] }],
      stances: [],
      default_stance: null,
      limitations: ['No live broker connector.'],
    },
  ],
  loop: {},
}

const INTENTS = {
  intents: [
    {
      intent: 'test_an_idea',
      label: 'Test an idea',
      detail: 'Freeze a hypothesis, run it over the data you hold, and judge the result.',
      actions: ['create_strategy', 'backtest_strategy', 'validate_strategy'],
      sections: { normal: ['strategies', 'validation'] },
      modes: ['normal'],
      caveat: 'The hypothesis is frozen before the run.',
    },
    {
      intent: 'manage_prop_accounts',
      label: 'Manage prop accounts',
      detail: 'Connect accounts and record what each programme permits.',
      actions: ['assess_prop_account'],
      sections: { prop_firm: ['desk', 'allocation'] },
      modes: ['prop_firm'],
      caveat:
        'No live broker connector exists in this build. Accounts connect to AlgoForge’s own simulator.',
    },
  ],
}

const EXPERTISE = {
  levels: [
    {
      level: 'guided',
      label: 'Guided',
      detail: 'Decisions, and the reason for each one.',
      surfaces: [{ surface: 'refusal_ladder', label: 'Why an order was refused', always_visible: true }],
    },
    {
      level: 'advanced',
      label: 'Advanced',
      detail: 'The inputs as well as the outputs.',
      surfaces: [{ surface: 'refusal_ladder', label: 'Why an order was refused', always_visible: true }],
    },
    {
      level: 'quant',
      label: 'Quant',
      detail: 'The method itself.',
      surfaces: [{ surface: 'trial_matrix', label: 'Trial matrix', always_visible: false }],
    },
  ],
  always_visible: ['refusal_ladder'],
}

let routes: Record<string, unknown>

function draw() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ModeSelect />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  routes = {
    '/modes': MODES,
    '/modes/intents': INTENTS,
    '/modes/expertise': EXPERTISE,
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = String(url).replace(/^.*\/api\/v1/, '')
      const body = routes[path]
      if (body === undefined) {
        return new Response(JSON.stringify({ detail: { reason: 'no fixture' } }), { status: 404 })
      }
      return new Response(JSON.stringify({ data: body }), { status: 200 })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the front door routes into the action registry', () => {
  test('every intent is offered by name', async () => {
    draw()
    expect(await screen.findByText('Test an idea')).toBeInTheDocument()
    expect(screen.getByText('Manage prop accounts')).toBeInTheDocument()
  })

  test('choosing one shows the actions it would run, not a conversation', async () => {
    draw()
    fireEvent.click(await screen.findByText('Test an idea'))
    expect(
      await screen.findByText(/create_strategy, backtest_strategy, validate_strategy/),
    ).toBeInTheDocument()
  })

  test('the caveat is shown at the front door rather than three screens in', async () => {
    draw()
    fireEvent.click(await screen.findByText('Manage prop accounts'))
    expect(
      await screen.findByText(/No live broker connector exists in this build/),
    ).toBeInTheDocument()
  })
})

describe('the depth selector is a preference, not a permission', () => {
  test('all three depths are offered', async () => {
    draw()
    expect(await screen.findByText('Guided')).toBeInTheDocument()
    expect(screen.getByText('Advanced')).toBeInTheDocument()
    expect(screen.getByText('Quant')).toBeInTheDocument()
  })

  test('it says plainly that a shallower depth removes nothing', async () => {
    draw()
    expect(
      await screen.findByText(/refusals, limitations, what was not\s+measured/),
    ).toBeInTheDocument()
  })
})

describe('a declaration that does not arrive does not take the screen down', () => {
  test('the workspaces still render when the intent endpoint answers oddly', async () => {
    routes['/modes/intents'] = { unexpected: true }
    routes['/modes/expertise'] = {}
    draw()
    expect(
      await screen.findByRole('heading', { name: /choose your workspace/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText('Test an idea')).not.toBeInTheDocument()
  })
})
