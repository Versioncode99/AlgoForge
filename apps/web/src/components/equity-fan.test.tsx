import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FanPoint } from '../fan'

vi.mock('echarts-for-react/lib/core', () => ({ default: () => <div data-testid="chart" /> }))

const { EquityFan } = await import('./EquityFan')

const points: FanPoint[] = [
  { day: 0, p05: 50000, p25: 50000, median: 50000, p75: 50000, p95: 50000, live: 200, resolved: 0 },
  { day: 1, p05: 49200, p25: 49700, median: 50100, p75: 50500, p95: 51100, live: 200, resolved: 0 },
  { day: 2, p05: 47800, p25: 49100, median: 50200, p75: 51300, p95: 52600, live: 180, resolved: 20 },
  { day: 3, p05: 46500, p25: 48600, median: 50400, p75: 52000, p95: 54000, live: 0, resolved: 200 },
]

describe('the scrubbable equity fan', () => {
  it('opens on the last day, which is the outcome the reader came for', () => {
    render(<EquityFan points={points} start={50000} />)
    const scrub = screen.getByLabelText('Day to read the distribution at') as HTMLInputElement
    expect(scrub.value).toBe('3')
    expect(screen.getByText('$46,500')).toBeInTheDocument()
    expect(screen.getByText('$54,000')).toBeInTheDocument()
  })

  it('reads a different day when scrubbed, from a keyboard-reachable control', () => {
    render(<EquityFan points={points} start={50000} />)
    const scrub = screen.getByLabelText('Day to read the distribution at')
    fireEvent.change(scrub, { target: { value: '1' } })
    expect(screen.getByText('$49,200')).toBeInTheDocument()
    expect(screen.getByText('$51,100')).toBeInTheDocument()
    expect(screen.queryByText('$46,500')).not.toBeInTheDocument()
  })

  it('says when every account is still open, and when they are not', () => {
    render(<EquityFan points={points} start={50000} />)
    const scrub = screen.getByLabelText('Day to read the distribution at')
    fireEvent.change(scrub, { target: { value: '1' } })
    expect(screen.getByText(/All 200 accounts were still trading/)).toBeInTheDocument()

    fireEvent.change(scrub, { target: { value: '2' } })
    expect(screen.getByText(/accounts leaving, not agreement/)).toBeInTheDocument()
  })

  it('cannot be scrubbed past either end of the fan', () => {
    render(<EquityFan points={points} start={50000} />)
    const scrub = screen.getByLabelText('Day to read the distribution at') as HTMLInputElement
    expect(scrub.min).toBe('0')
    expect(scrub.max).toBe('3')
    fireEvent.change(scrub, { target: { value: '99' } })
    expect(screen.getByText('$46,500')).toBeInTheDocument()
  })

  it('refuses to draw a fan it was given nothing for', () => {
    render(<EquityFan points={[]} start={50000} />)
    expect(screen.getByText(/No fan/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Day to read the distribution at')).not.toBeInTheDocument()
  })
})
