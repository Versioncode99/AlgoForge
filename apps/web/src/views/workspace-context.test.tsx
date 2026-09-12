import '@testing-library/jest-dom/vitest'
import { expect, test } from 'vitest'
import type { ResolvedPanel } from '../workstation'
import { symbolFor } from '../workstation'

/* The resolution the workspace grid does at render.
 *
 * Kept as unit tests over the two pure helpers rather than as a render of the
 * whole grid: the grid needs a workspace, a dataset registry, a drag layer and
 * a chart library, and none of that is what is being pinned here.
 */

const RESOLVED: ResolvedPanel[] = [
  { panel_id: 'chart-1', symbol: 'NQ', timeframe: '1m', group: 'a', source: 'context' },
  { panel_id: 'chart-2', symbol: 'ES', timeframe: '5m', group: null, source: 'panel' },
  { panel_id: 'chart-3', symbol: 'CL', timeframe: '', group: 'b', source: 'panel' },
]

/* Mirrors `titleFor`/`settingsFor` in the view. Duplicated deliberately: the
 * view's copies close over a query result, and extracting them would be an
 * abstraction invented for a test rather than for the code. */
const title = (panel: { panel_id: string; title: string }) => {
  const shown = symbolFor(panel.panel_id, RESOLVED)
  if (!shown || shown.source !== 'context' || !shown.symbol) return panel.title
  return `${shown.symbol} ${shown.timeframe}`.trim()
}

const settings = (panel: { panel_id: string; settings: Record<string, unknown> }) => {
  const shown = symbolFor(panel.panel_id, RESOLVED)
  if (!shown || shown.source !== 'context') return panel.settings
  return {
    ...panel.settings,
    ...(shown.symbol ? { symbol: shown.symbol } : {}),
    ...(shown.timeframe ? { timeframe: shown.timeframe } : {}),
    dataset: '',
  }
}

test('a following panel is titled with what it shows, not what it stores', () => {
  // Caught in QA on the running application: a panel pinned to MNQ and linked
  // to a context on NQ drew "MNQ 5m" over a body reading "no archive for NQ".
  expect(title({ panel_id: 'chart-1', title: 'MNQ 5m' })).toBe('NQ 1m')
})

test('a panel that is not following keeps its own title', () => {
  expect(title({ panel_id: 'chart-2', title: 'ES 5m' })).toBe('ES 5m')
})

test('a panel in a group whose context sets nothing keeps its own title', () => {
  expect(title({ panel_id: 'chart-3', title: 'CL 1h' })).toBe('CL 1h')
})

test('a panel not in the resolved set at all keeps its own title', () => {
  expect(title({ panel_id: 'nowhere', title: 'Watchlist' })).toBe('Watchlist')
})

test('following overlays the symbol and drops the stored dataset', () => {
  /* `PanelBody` resolves `settings.dataset` before `settings.symbol`, so a
   * stored dataset would win over the followed symbol and the panel would keep
   * drawing the old archive while its header said otherwise. */
  const result = settings({
    panel_id: 'chart-1',
    settings: { symbol: 'MNQ', timeframe: '5m', dataset: 'mnq_1m_7y', indicator: 'vwap' },
  })
  expect(result).toEqual({ symbol: 'NQ', timeframe: '1m', dataset: '', indicator: 'vwap' })
})

test('a panel that is not following is handed its settings untouched', () => {
  const own = { symbol: 'ES', timeframe: '5m', dataset: 'es_1m_10y' }
  expect(settings({ panel_id: 'chart-2', settings: own })).toBe(own)
})

test('a context that sets no timeframe does not blank the panel’s own', () => {
  const resolved: ResolvedPanel[] = [
    { panel_id: 'p', symbol: 'NQ', timeframe: '', group: 'a', source: 'context' },
  ]
  const shown = symbolFor('p', resolved)
  expect(shown?.timeframe).toBe('')
  // The overlay only spreads facets the context actually sets.
  const merged = { symbol: 'MNQ', timeframe: '5m', ...(shown?.symbol ? { symbol: shown.symbol } : {}), ...(shown?.timeframe ? { timeframe: shown.timeframe } : {}) }
  expect(merged).toEqual({ symbol: 'NQ', timeframe: '5m' })
})
