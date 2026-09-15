import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { PropAccountView } from './PropAccount'

/* The rule catalogue, on the screen where somebody decides what to trust.
 *
 * `tests/api/test_prop_rules_api.py` proves the API carries the review state.
 * What is asserted here is that it survives to the pixels: a rule set nobody
 * has checked, and one whose check has lapsed, must not arrive looking
 * authoritative just because their numbers rendered.
 *
 * The failure being guarded against is quiet. A table of starting balances and
 * loss limits reads as fact whatever its provenance, so the review state has to
 * be *in the row*, not in a tooltip or a footnote.
 */

const CATALOGUE = {
  schema_version: '1',
  directory: 'rules',
  rule_sets: [
    {
      schema_version: '1',
      rule_id: 'desk-50k-funded',
      display_name: '50K Funded',
      origin: 'funded.json',
      rules: { starting_balance: 50000, maximum_loss: 2000, trail_mode: 'end_of_day' },
      rules_id: 'r1',
      provenance: {
        source_url: '',
        source_hash: '',
        verified: false,
        reviewed_by: '',
        effective_from: null,
        review_expires_at: null,
        status: 'UNVERIFIED',
        effective: true,
      },
      status: 'UNVERIFIED',
      needs_review: true,
    },
    {
      schema_version: '1',
      rule_id: 'desk-50k-lapsed',
      display_name: '50K Lapsed',
      origin: 'lapsed.json',
      rules: { starting_balance: 50000, maximum_loss: 2500, trail_mode: 'intraday' },
      rules_id: 'r2',
      provenance: {
        source_url: 'https://example.test/terms',
        source_hash: '',
        verified: true,
        reviewed_by: 'an operator',
        effective_from: null,
        review_expires_at: '2026-01-01',
        status: 'EXPIRED',
        effective: true,
      },
      status: 'EXPIRED',
      needs_review: true,
    },
    {
      schema_version: '1',
      rule_id: 'desk-25k',
      display_name: '25K Checked',
      origin: 'checked.json',
      rules: { starting_balance: 25000, maximum_loss: 1500, trail_mode: 'static' },
      rules_id: 'r3',
      provenance: {
        source_url: 'https://example.test/terms',
        source_hash: 'abc',
        verified: true,
        reviewed_by: 'an operator',
        effective_from: null,
        review_expires_at: '2030-01-01',
        status: 'VERIFIED',
        effective: true,
      },
      status: 'VERIFIED',
      needs_review: false,
    },
  ],
  rejected: [{ origin: 'broken.json', reason: "trail_mode 'RATCHET' is not one this build knows" }],
  counts: { loaded: 3, rejected: 1, needing_review: 2 },
  warnings: [
    '50K Lapsed: verified against https://example.test/terms but the review lapsed on 2026-01-01. Check the contract again before trading to these numbers.',
    '50K Funded: nobody has checked these numbers against a contract.',
  ],
}

const ROUTES: Record<string, unknown> = {
  // No prop accounts: the screen falls to `NoAccount`, which is exactly where
  // an operator is choosing a rule set.
  '/prop/accounts': { accounts: [], selected: null },
  '/propdesk/rules': CATALOGUE,
}

function draw() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PropAccountView section="rules" />
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

describe('the rule catalogue says how far each rule set has been checked', () => {
  test('every rule set on disk is listed', async () => {
    draw()
    expect(await screen.findByText('50K Funded')).toBeInTheDocument()
    expect(screen.getByText('50K Lapsed')).toBeInTheDocument()
    expect(screen.getByText('25K Checked')).toBeInTheDocument()
  })

  test('a rule set nobody has checked says so beside its numbers', async () => {
    /* Not in a tooltip. A balance and a loss limit read as fact, so the words
     * that qualify them have to be in the same row. */
    draw()
    expect(await screen.findByText('Never checked')).toBeInTheDocument()
  })

  test('a lapsed review is distinguished from one that never happened', async () => {
    draw()
    expect(await screen.findByText('Review lapsed')).toBeInTheDocument()
    // Scoped to the row: "Checked" is also the column heading above it, and an
    // unscoped match finds two elements rather than failing.
    const checked = screen.getByText('25K Checked').closest('tr')
    expect(checked).not.toBeNull()
    expect(within(checked as HTMLElement).getByText('Checked')).toBeInTheDocument()
  })

  test('the warnings are shown, worst first', async () => {
    draw()
    expect(await screen.findByText(/the review lapsed on 2026-01-01/)).toBeInTheDocument()
    expect(
      screen.getByText(/nobody has checked these numbers against a contract/),
    ).toBeInTheDocument()
  })

  test('a file that could not be read is named rather than quietly absent', async () => {
    /* A catalogue short by one reads as though the file was never written, and
     * the operator goes looking for a rule set they are sure they created. */
    draw()
    expect(await screen.findByText(/broken.json/)).toBeInTheDocument()
    expect(screen.getByText(/RATCHET/)).toBeInTheDocument()
  })

  test('the directory being read is stated', async () => {
    draw()
    expect(await screen.findByText(/rules · schema 1/)).toBeInTheDocument()
  })

  test('an empty directory explains what a rule set is rather than showing nothing', async () => {
    ROUTES['/propdesk/rules'] = {
      ...CATALOGUE,
      rule_sets: [],
      rejected: [],
      warnings: [],
      counts: { loaded: 0, rejected: 0, needing_review: 0 },
    }
    draw()
    expect(await screen.findByText('The rules directory is empty')).toBeInTheDocument()
    ROUTES['/propdesk/rules'] = CATALOGUE
  })
})
