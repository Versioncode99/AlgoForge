import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import { BOUNDARY, Wordmark } from './Logo'

/* The one claim this application makes on every screen.
 *
 * "PAPER ONLY" sits under the wordmark and is the only place the product states
 * that nothing here reaches a live account. It used to have a sentence beside
 * it — "no broker, venue or order-routing vendor is connected" — on the mode
 * chooser, and removing the chooser took the sentence with it. Two words on
 * their own read as a setting somebody could switch off, which is the opposite
 * of what they mean.
 *
 * So the claim is pinned here: the words, the sentence, and the fact that the
 * sentence reaches a screen reader rather than only a pointer.
 */

afterEach(cleanup)

describe('the paper-only boundary states itself', () => {
  test('the label is rendered', () => {
    render(<Wordmark />)
    expect(screen.getByText('PAPER ONLY')).toBeInTheDocument()
  })

  test('the sentence explaining it is available to a screen reader', () => {
    /* `title` alone is not enough: it is a pointer affordance, and the people
     * most likely to misread a two-word label are not hovering it. */
    render(<Wordmark />)
    const explanation = screen.getByText(BOUNDARY, { selector: '.sr-only' })
    expect(explanation).toBeInTheDocument()
  })

  test('the sentence is also a pointer affordance', () => {
    render(<Wordmark />)
    expect(screen.getByText('PAPER ONLY')).toHaveAttribute('title', BOUNDARY)
  })

  test('the sentence says what is absent, not merely what the mode is', () => {
    /* The failure being prevented is a sentence that says "paper trading is
     * enabled" — which implies a switch, and there is no switch. What makes the
     * claim true is that no connector exists. */
    expect(BOUNDARY).toMatch(/no broker, venue or order-routing connector/i)
    expect(BOUNDARY).toMatch(/backtest|simulator/i)
    expect(BOUNDARY).toMatch(/never/i)
  })
})
