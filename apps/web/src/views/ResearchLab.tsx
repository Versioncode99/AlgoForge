import { useQuery } from '@tanstack/react-query'
import { Database, FlaskConical, LockKeyhole, TableProperties } from 'lucide-react'
import { useState } from 'react'
import { getJson } from '../api'
import { pct, signed } from '../lib'
import type { ResearchOverview } from '../types'
import { ResearchBrain } from './AgentCommand'

type Pane = 'families' | 'matrix' | 'capabilities' | 'papers'

/** A research inventory, not a leaderboard. Empty cells and locked families are
 *  first-class so the screen cannot imply breadth that has not been tested. */
export function ResearchLabView() {
  const [pane, setPane] = useState<Pane>('families')
  const overview = useQuery({
    queryKey: ['research-overview'],
    queryFn: () => getJson<ResearchOverview>('/research/overview'),
  })

  if (overview.isPending) return <div className="state">Loading research inventory...</div>
  if (!overview.data) return <div className="state error">Research inventory unavailable.</div>
  const data = overview.data
  const tested = data.families.reduce((sum, item) => sum + item.tested_count, 0)
  const oos = data.families.reduce((sum, item) => sum + item.validation_oos_count, 0)
  const holdout = data.families.reduce((sum, item) => sum + item.holdout_count, 0)
  const locked = data.catalog.filter((item) => !item.runnable).length

  return (
    <section className="research-lab stack">
      <div className="section-title research-title">
        <p className="view-note">Which families have evidence, which are only ideas, and which the installed providers cannot serve at all. An untested cell stays untested; it is never filled with a zero.</p>
        <span className="evidence-badge">NO EMPTY-CELL IMPUTATION</span>
      </div>

      <div className="metrics research-metrics">
        <div><span>Runnable families</span><strong>{data.families.length}</strong><small>implemented templates</small></div>
        <div><span>Tested variants</span><strong>{tested}</strong><small>saved backtest artifacts</small></div>
        <div><span>Validation / holdout</span><strong>{oos} / {holdout}</strong><small>chronological evidence only</small></div>
        <div><span>Capability locks</span><strong className="warn">{locked}</strong><small>data or engine missing</small></div>
      </div>

      <div className="research-switch" role="tablist" aria-label="Research views">
        <button className={pane === 'papers' ? 'active' : ''} onClick={() => setPane('papers')}>
          <FlaskConical /> Research papers
        </button>
        <button className={pane === 'families' ? 'active' : ''} onClick={() => setPane('families')}>
          <FlaskConical /> Families
        </button>
        <button className={pane === 'matrix' ? 'active' : ''} onClick={() => setPane('matrix')}>
          <TableProperties /> Forge matrix
        </button>
        <button className={pane === 'capabilities' ? 'active' : ''} onClick={() => setPane('capabilities')}>
          <Database /> Capability map
        </button>
      </div>
      {pane === 'papers' && <ResearchBrain />}

      {pane === 'families' && (
        <div className="family-grid">
          {data.families.map((item) => (
            <article className="family-card" key={item.key}>
              <header>
                <div><span>{item.family}</span><h3>{item.name}</h3></div>
                <b>{item.tested_count ? 'TESTED' : 'UNTESTED'}</b>
              </header>
              <div className="family-kpis">
                <div><span>Variants</span><strong>{item.variant_count}</strong></div>
                <div><span>Positive</span><strong className={(item.positive_share ?? 0) > .5 ? 'good' : ''}>
                  {item.positive_share == null ? '--' : pct(item.positive_share)}
                </strong></div>
                <div><span>Median exp.</span><strong className={(item.median_expectancy ?? 0) >= 0 ? 'good' : 'bad'}>
                  {item.median_expectancy == null ? '--' : signed(item.median_expectancy)}
                </strong></div>
                <div><span>Best exp.</span><strong>{item.best_expectancy == null ? '--' : signed(item.best_expectancy)}</strong></div>
              </div>
              <footer>
                <span>{item.required_data}</span>
                <span className={item.holdout_count ? 'good' : ''}>OOS {item.validation_oos_count} / H {item.holdout_count}</span>
              </footer>
            </article>
          ))}
        </div>
      )}

      {pane === 'matrix' && (
        <div className="panel matrix-panel">
          <header><h2>Strategy x market evidence matrix</h2><span>Blank means not tested, never zero</span></header>
          <div className="panel-body matrix-scroll">
            <table className="research-matrix">
              <thead><tr><th>Family</th>{data.matrix.markets.map((market) => <th key={market}>{market}</th>)}</tr></thead>
              <tbody>{data.matrix.rows.map((row) => (
                <tr key={row.key}><th>{row.name}</th>{row.cells.map((cell) => (
                  <td key={cell.market} data-status={cell.status}>
                    {cell.expectancy == null ? <span>NOT TESTED</span> : <><b className={cell.expectancy >= 0 ? 'good' : 'bad'}>{signed(cell.expectancy)}</b><small>{cell.evidence_tier}</small></>}
                  </td>
                ))}</tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}

      {pane === 'capabilities' && (
        <div className="capability-grid">
          {data.catalog.map((item) => (
            <article className={`capability-card ${item.runnable ? '' : 'is-locked'}`} key={item.key}>
              <header>
                <div className="capability-icon">{item.runnable ? <FlaskConical /> : <LockKeyhole />}</div>
                <div><span>{item.family} / {item.minimum_timeframe}</span><h3>{item.name}</h3></div>
                <b className={item.runnable ? 'good' : 'warn'}>{item.status.replaceAll('_', ' ')}</b>
              </header>
              <p>{item.description}</p>
              <div className="capability-data">{item.required_data.map((value) => <span key={value}>{value}</span>)}</div>
              {item.missing_capability && <footer>{item.missing_capability}</footer>}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
