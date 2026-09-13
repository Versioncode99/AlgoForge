import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PortPanel } from './PortPanel'

/* The port surface, and the claim it must not let a reader make.
 *
 * A panel whose first control is "Generate" is the shape of the problem: a file
 * that compiles gets pasted into TradingView and somebody trades a strategy
 * that is not the one they validated. So what leads is the ledger of
 * differences, and the status is capped by what was actually checked.
 */

const TARGETS = {
  targets: [
    { key: 'python', label: 'Python', generates: true, ceiling: 'verified', language: 'python', note: 'Generated and checked.' },
    { key: 'pine', label: 'Pine Script v6', generates: true, ceiling: 'structural', language: 'pinescript', note: 'Generated but not executed.' },
    { key: 'mql5', label: 'MQL5', generates: false, ceiling: 'structural', language: 'mql5', note: 'Analysed, not generated.' },
  ],
}

let report: unknown = null

vi.mock('../api', () => ({
  getJson: vi.fn(async (path: string) => {
    if (path.endsWith('/port-targets')) return TARGETS
    return report
  }),
}))

function portReport(over: Record<string, unknown> = {}) {
  return {
    target: 'pine',
    definition_id: 'sdef_1',
    definition_hash: 'abc',
    status: 'approximate',
    code: '// @version=6\nstrategy("x")',
    elements: [
      { part: 'feature', name: 'atr14 (atr)', fidelity: 'equivalent', detail: '', rendered: 'ta.atr(14)' },
      {
        part: 'exit',
        name: 'trailing stop (feature)',
        fidelity: 'approximated',
        detail: 'Pine ratchets intrabar; AlgoForge ratchets on closed bars only.',
        rendered: '',
      },
    ],
    notes: ['Nothing here claims the logic is preserved.'],
    counts: { equivalent: 1, approximated: 1, unsupported: 0 },
    ...over,
  }
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PortPanel strategyId="s1" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  report = portReport()
})

describe('PortPanel', () => {
  it('leads with what did not cross, not with the code', async () => {
    const { container } = mount()
    expect(await screen.findByText('trailing stop (feature)')).toBeInTheDocument()
    expect(screen.getByText(/ratchets intrabar/)).toBeInTheDocument()

    // The code exists and is folded away behind the differences. Asserted as
    // the disclosure being closed rather than as the text being absent:
    // `<details>` keeps its content in the DOM either way, so "not in the
    // document" would pass for a panel that shows the code first.
    const disclosure = container.querySelector('details.port-code')!
    expect(disclosure).toBeTruthy()
    expect(disclosure.hasAttribute('open')).toBe(false)
    expect(screen.getByText(/Generated Pine Script v6/)).toBeInTheDocument()
    // And it sits after the elements in document order.
    const elements = container.querySelector('.port-elements')!
    expect(elements.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('hides what crossed cleanly until asked', async () => {
    /* Those are the elements nobody needs to read. Listing them first buries
     * the three lines that decide whether the port is usable. */
    mount()
    await screen.findByText('trailing stop (feature)')
    expect(screen.queryByText('atr14 (atr)')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /crossed cleanly/ }))
    expect(screen.getByText('atr14 (atr)')).toBeInTheDocument()
  })

  it('explains the status rather than only labelling it', async () => {
    mount()
    expect(await screen.findByText('Approximate')).toBeInTheDocument()
    expect(screen.getByText(/difference that can change results/)).toBeInTheDocument()
  })

  it('distinguishes structural from verified in words, not only colour', async () => {
    /* "The pieces line up" and "somebody ran both and compared the trades" are
     * different claims, and adjacent green badges would read as one. */
    report = portReport({ status: 'structural' })
    mount()
    expect(await screen.findByText('Structural')).toBeInTheDocument()
    expect(screen.getByText(/Nothing executed it/)).toBeInTheDocument()
    report = portReport({ status: 'verified' })
  })

  it('says an incomplete port is a starting point and not the strategy', async () => {
    report = portReport({
      status: 'incomplete',
      elements: [
        {
          part: 'feature',
          name: 'orh (opening_range_high)',
          fidelity: 'unsupported',
          detail: 'No Pine builtin; needs a hand-written session accumulator.',
          rendered: '',
        },
      ],
      counts: { equivalent: 0, approximated: 0, unsupported: 1 },
    })
    mount()
    expect(await screen.findByText('Incomplete')).toBeInTheDocument()
    expect(screen.getByText(/starting point and not the strategy/)).toBeInTheDocument()
    expect(screen.getByText('Unsupported')).toBeInTheDocument()
  })

  it('says on the chooser which targets are only analysed', async () => {
    /* A different offer, and one that should not be discovered after picking. */
    mount()
    expect(await screen.findByText('MQL5')).toBeInTheDocument()
    expect(screen.getByText('analysed only')).toBeInTheDocument()
    expect(screen.getAllByText('generated')).toHaveLength(2)
  })

  it('explains the absence of a file rather than showing an empty box', async () => {
    report = portReport({ target: 'mql5', code: '', status: 'approximate' })
    const { container } = mount()
    fireEvent.click(await screen.findByRole('button', { name: /MQL5/ }))
    expect(await screen.findByText(/No file is produced for this target/)).toBeInTheDocument()
    expect(container.querySelector('details.port-code')).toBeNull()
  })

  it('says so when everything mapped natively', async () => {
    report = portReport({
      status: 'structural',
      elements: [
        { part: 'feature', name: 'atr14 (atr)', fidelity: 'equivalent', detail: '', rendered: '' },
      ],
      counts: { equivalent: 1, approximated: 0, unsupported: 0 },
    })
    mount()
    expect(await screen.findByText('Every element mapped natively.')).toBeInTheDocument()
  })

  it('reports a refusal instead of rendering nothing', async () => {
    const { getJson } = await import('../api')
    vi.mocked(getJson).mockImplementation(async (path: string) => {
      if (path.endsWith('/port-targets')) return TARGETS
      throw new Error('porting renders the Strategy IR, and this strategy is hand-written Python.')
    })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('hand-written Python')
  })
})
