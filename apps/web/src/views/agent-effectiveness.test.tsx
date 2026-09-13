import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import type { ControlCenter } from '../research'
import { AgentEffectiveness } from './AgentEffectiveness'

/* The panel exists because an experiment count looks like work.
 *
 * An agent that proposed forty things and had thirty-eight refused reports two
 * experiments and nothing anywhere said the other thirty-eight happened. These
 * assert that the refusals are shown beside the experiments, and that the three
 * ways this panel could mislead are closed.
 */

type Effectiveness = ControlCenter['effectiveness']

const DATA: Effectiveness = {
  roles: [
    {
      role: 'DISCOVERY', agents: 2, proposals: 40, experiments: 2, refusals: 38,
      idle_cycles: 0, findings: 0, errors: 0, compute_spent: 0,
      acceptance: 0.05, efficiency: 0, efficiency_measured: false, measured: true,
    },
    {
      role: 'REGIME', agents: 1, proposals: 10, experiments: 8, refusals: 2,
      idle_cycles: 5, findings: 3, errors: 0, compute_spent: 4,
      acceptance: 0.8, efficiency: 2, efficiency_measured: true, measured: true,
    },
  ],
  totals: { experiments: 10, refusals: 40, idle_cycles: 5, findings: 3, acceptance: 0.2 },
  weakest_role: 'DISCOVERY',
  weakest_acceptance: 0.05,
  note: 'Acceptance is the share of an agent’s proposals that became an experiment.',
}

afterEach(cleanup)

test('refusals are shown beside the experiments that hid them', () => {
  render(<AgentEffectiveness data={DATA} />)
  // Scoped to the table: the weakest-role sentence below it names the same role,
  // which is the point of that sentence and not a duplicate row.
  const table = within(screen.getByRole('table'))
  const row = table.getByText('DISCOVERY').closest('tr')!
  expect(within(row).getByText('40')).toBeInTheDocument()
  expect(within(row).getByText('38')).toBeInTheDocument()
  expect(within(row).getByText('5%')).toBeInTheDocument()
})

test('unmetered efficiency says so rather than showing zero', () => {
  render(<AgentEffectiveness data={DATA} />)
  const table = within(screen.getByRole('table'))
  const discovery = table.getByText('DISCOVERY').closest('tr')!
  expect(within(discovery).getByText('not measured')).toBeInTheDocument()
  const regime = table.getByText('REGIME').closest('tr')!
  expect(within(regime).getByText('2.00')).toBeInTheDocument()
})

test('the weakest role is named with the reason it is usually low', () => {
  render(<AgentEffectiveness data={DATA} />)
  expect(screen.getByText(/exhausted rather than the/)).toBeInTheDocument()
})

test('no proposals is reported as nothing to measure, not as a score of zero', () => {
  render(<AgentEffectiveness data={{ ...DATA, roles: [] }} />)
  expect(screen.getByText(/nothing to measure rather than a score of zero/)).toBeInTheDocument()
})

test('a payload the server did not send does not take the screen down', () => {
  render(<AgentEffectiveness data={undefined} />)
  expect(screen.getByText('Agent effectiveness')).toBeInTheDocument()
})
