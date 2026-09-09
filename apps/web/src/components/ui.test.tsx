/* The rolling number, interrupted.
 *
 * Values here change while the previous animation is still running — a job
 * poll landing early, a re-render on new data — and that is the case the
 * component got wrong. */

import { render, screen } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Rolling } from './ui'

/** Hand-driven clock and frame loop, so the assertions are about the
 *  component's arithmetic rather than about how fast the machine is. */
let now = 0
let frames: Array<(t: number) => void> = []

beforeEach(() => {
  now = 0
  frames = []
  vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => {
    frames.push(cb)
    return frames.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  vi.stubGlobal('performance', { now: () => now })
  // Not reduced motion: the component short-circuits to a snap otherwise.
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
})

afterEach(() => vi.unstubAllGlobals())

/** Advance the clock and run whatever frames were queued. */
function advance(ms: number) {
  now += ms
  const queued = frames
  frames = []
  act(() => {
    queued.forEach(cb => cb(now))
  })
}

const shown = () => Number(screen.getByTestId('n').textContent!.replace(/,/g, ''))

function Probe({ value }: { value: number }) {
  return (
    <span data-testid="n">
      <Rolling value={value} />
    </span>
  )
}

describe('Rolling', () => {
  it('does not jump away from the target when interrupted mid-roll', () => {
    /* The animation is 380ms. Cleanup used to set the origin to the value it
     * had been animating *towards*, not to the number actually on screen. So
     * interrupting a 0 -> 100 roll at 83 and retargeting to 50 restarted from
     * 100: the digits jumped up to a number the count had never reached, then
     * rolled back down. The origin has to be what the reader is looking at. */
    const { rerender } = render(<Probe value={0} />)
    rerender(<Probe value={100} />)
    advance(120)

    const midway = shown()
    expect(midway).toBeGreaterThan(0)
    expect(midway).toBeLessThan(100)

    // Retarget to something between the current reading and zero.
    rerender(<Probe value={50} />)
    advance(16)

    // It must move toward 50 from where it was, never overshoot past the
    // reading it interrupted.
    expect(shown()).toBeLessThanOrEqual(midway + 0.5)
  })

  it('still arrives exactly on the target', () => {
    const { rerender } = render(<Probe value={0} />)
    rerender(<Probe value={100} />)
    advance(400)
    expect(shown()).toBe(100)
  })
})
