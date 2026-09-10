import { useState } from 'react'
import { ArrowRight, CircleSlash, ShieldAlert } from 'lucide-react'
import {
  Limitations, MeasureRow, MeasureTable, NotMeasured, StatusPill, type Tone,
} from '../components/measures'
import { Empty, PanelHead, Stat } from '../components/ui'
import {
  useApprovalDecision, useApprovals, useAudit, useFundActions, useFundOperations,
  useFundPerformance, useFundRisk, useFundSignals, useFundState, useScreenedOrders,
  type GateCheck, type PortfolioProposal, type RiskMeasure, type ScreenedOrder, type StageState,
} from '../fund'

/* Hedge Fund mode: the loop, and its state.
 *
 * The command centre is a status board rather than a diagram. Every stage
 * carries what it is actually doing — "3 candidates eligible", "within limits",
 * "2 of 5 blocked" — and clicking one opens it. A stage nobody could measure
 * says "not measured" and means it: this build has no mark-to-market feed, so
 * an open position's unrealised P&L is absent rather than drawn as zero.
 *
 * The rebalance is deliberately four buttons and not one. Construct proposes,
 * screen refuses, submit routes; a single "rebalance" control would put the
 * pre-trade gate inside a step nobody can decline, which is exactly the shape
 * this architecture exists to avoid.
 */

type SectionKey =
  | 'fund' | 'alpha' | 'portfolio' | 'risk' | 'gate'
  | 'execution' | 'operations' | 'performance' | 'approvals' | 'audit'

const STAGE_TONE: Record<StageState['status'], Tone> = {
  idle: 'plain',
  running: 'plain',
  ready: 'good',
  warning: 'warn',
  blocked: 'bad',
  halted: 'bad',
  unknown: 'unknown',
}

export function FundView({ section }: { section: SectionKey }) {
  switch (section) {
    case 'fund': return <CommandCentre />
    case 'alpha': return <AlphaSection />
    case 'portfolio': return <PortfolioSection />
    case 'risk': return <RiskSection />
    case 'gate': return <GateSection />
    case 'execution': return <ExecutionSection />
    case 'operations': return <OperationsSection />
    case 'performance': return <PerformanceSection />
    case 'approvals': return <ApprovalsSection />
    case 'audit': return <AuditSection />
  }
}

// ── command centre ───────────────────────────────────────────────────────────

function CommandCentre() {
  const state = useFundState()
  if (state.isPending) return <div className="state" role="status">Reading the fund…</div>
  if (state.isError) {
    return <Empty title="The fund could not be read" detail={(state.error as Error).message} />
  }
  const fund = state.data!
  const risk = fund.risk

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="NAV" value={money(fund.nav)} note="cash plus positions at average fill" />
        <Stat label="Cash" value={money(fund.cash)} />
        <Stat
          label="Realised P&L"
          value={money(fund.realised_pnl)}
          tone={fund.realised_pnl > 0 ? 'good' : fund.realised_pnl < 0 ? 'bad' : 'plain'}
        />
        <Stat label="Gross" value={ratio(fund.gross_exposure)} note="of capital" />
        <Stat label="Net" value={ratio(fund.net_exposure)} note="of capital" />
        <Stat label="Leverage" value={`${fund.leverage.toFixed(2)}×`} />
        <Stat
          label="Risk"
          value={risk.enabled ? (risk.within_limits ? 'WITHIN LIMITS' : `${risk.breaches.length} BREACH`) : 'HALTED'}
          tone={!risk.enabled || !risk.within_limits ? 'bad' : 'good'}
          note={risk.enabled ? risk.limits_name : 'kill switch engaged'}
        />
        <Stat label="Execution" value={fund.execution_mode} note="simulated locally" />
      </section>

      {!risk.enabled && (
        <p className="fund-halt" role="alert">
          <ShieldAlert aria-hidden="true" />
          The <b>{risk.limits_name}</b> limit set is disabled. This is a kill switch: every proposed
          order is refused until a person re-enables it, and nothing an assistant does can change
          that.
        </p>
      )}

      <section className="fund-loop">
        <PanelHead title="The loop" meta="every stage has a state, and opens" />
        <ol className="loop-track">
          {fund.stages.map((stage, index) => (
            <li key={stage.stage}>
              <a href={`#${stage.route}`} data-status={stage.status} title={stage.purpose}>
                <span className="loop-index">{String(index + 1).padStart(2, '0')}</span>
                <b>{stage.label}</b>
                <StatusPill label={stage.summary} tone={STAGE_TONE[stage.status]} title={stage.summary} />
                {stage.detail && <em>{stage.detail}</em>}
              </a>
            </li>
          ))}
        </ol>
        {/* The ordinals carry the order; this carries the fact that it closes.
            Arrows between the cards could not: a grid does not know where a row
            ends, so every wrap left one pointing at nothing. */}
        <p className="loop-close">
          <ArrowRight aria-hidden="true" />
          Feedback returns to Research — the loop runs continuously rather than once.
        </p>
      </section>

      <Limitations items={fund.limitations} />
    </div>
  )
}

// ── alpha ────────────────────────────────────────────────────────────────────

function AlphaSection() {
  const signals = useFundSignals()
  if (signals.isPending) return <div className="state" role="status">Reading candidates…</div>
  const rows = signals.data?.signals ?? []

  return (
    <div className="fund-view af-panel-in">
      <section className="measure-panel">
        <PanelHead title="Candidate signals" meta={`${rows.length} in the configured universe`} />
        {rows.length === 0 ? (
          <NotMeasured
            what="No candidates"
            why="A candidate is a backtested strategy whose instrument is in the fund's universe. Add instruments to the universe, or build a strategy on one that is already there."
          />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Strategy</th>
                <th scope="col">Instrument</th>
                <th scope="col">Forecast</th>
                <th scope="col">Confidence</th>
                <th scope="col">Verdict</th>
                <th scope="col">Eligible</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((signal) => (
                <tr key={`${signal.strategy_id}-${signal.symbol}`} className="measure-row">
                  <th scope="row"><span className="mono">{signal.strategy_id}</span></th>
                  <td className="measure-value mono">{signal.symbol}</td>
                  <td className="measure-value">{(signal.expected_return * 100).toFixed(3)}%</td>
                  <td className="measure-value">{signal.confidence.toFixed(2)}</td>
                  <td className="measure-status">
                    <StatusPill
                      label={signal.verdict ?? 'NEVER JUDGED'}
                      tone={signal.verdict === 'PASS' ? 'good' : signal.verdict ? 'bad' : 'unknown'}
                    />
                  </td>
                  <td className="measure-detail">
                    {signal.verdict === 'PASS' ? 'may be sized' : 'excluded by the judge'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <Limitations items={signals.data?.limitations ?? []} />
    </div>
  )
}

// ── portfolio ────────────────────────────────────────────────────────────────

function PortfolioSection() {
  const { construct, screen } = useFundActions()
  const proposal = construct.data?.portfolio ?? null

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-actions">
        <button onClick={() => construct.mutate()} disabled={construct.isPending}>
          {construct.isPending ? 'Constructing…' : 'Construct portfolio'}
        </button>
        {proposal?.feasible && (
          <button
            className="is-secondary"
            onClick={() => screen.mutate(proposal.portfolio_id)}
            disabled={screen.isPending}
          >
            {screen.isPending ? 'Screening…' : 'Prepare and screen the rebalance'}
          </button>
        )}
        <p>
          Construction proposes. Nothing is screened until you ask, and nothing is placed until the
          gate has cleared it.
        </p>
      </section>

      {construct.isError && (
        <p className="form-error" role="alert">{(construct.error as Error).message}</p>
      )}
      {screen.isError && <p className="form-error" role="alert">{(screen.error as Error).message}</p>}
      {screen.data && (
        <p className="fund-note" role="status">
          {screen.data.cleared.length} cleared, {screen.data.blocked.length} blocked. Open{' '}
          <a href="#gate">Pre-Trade</a> for the reasons.
        </p>
      )}

      {!proposal ? (
        <NotMeasured
          what="No proposal"
          why="Construction runs on demand rather than on a timer. A portfolio sized an hour ago against prices that have moved is worse than none, so proposals are not stored between requests."
        />
      ) : (
        <Proposal proposal={proposal} />
      )}
    </div>
  )
}

function Proposal({ proposal }: { proposal: PortfolioProposal }) {
  return (
    <>
      {!proposal.feasible && (
        <p className="fund-halt" role="alert">
          <CircleSlash aria-hidden="true" />
          No portfolio was produced. The reasons are listed below; nothing was sized rather than
          something being sized on an estimate that does not exist.
        </p>
      )}

      <section className="fund-top">
        <Stat label="Expected return" value={`${(proposal.expected_return * 100).toFixed(3)}%`} />
        <Stat label="Expected volatility" value={`${(proposal.expected_volatility * 100).toFixed(3)}%`} />
        <Stat
          label="Expected Sharpe"
          value={proposal.expected_sharpe === null ? '—' : proposal.expected_sharpe.toFixed(2)}
          tone={proposal.expected_sharpe === null ? 'unknown' : 'plain'}
          note={proposal.expected_sharpe === null ? 'volatility is zero or unmeasured' : undefined}
        />
        <Stat label="Gross" value={ratio(proposal.exposures.gross)} />
        <Stat label="Net" value={ratio(proposal.exposures.net)} />
        <Stat label="Turnover" value={ratio(proposal.turnover)} />
        <Stat label="Estimated cost" value={money(proposal.estimated_cost)} note="charged in the objective" />
        <Stat label="Concentration" value={proposal.exposures.concentration.toFixed(3)} note="Herfindahl" />
      </section>

      <section className="measure-panel">
        <PanelHead title="Holdings" meta={proposal.optimiser} />
        {proposal.holdings.length === 0 ? (
          <NotMeasured what="Nothing sized" why="No signal survived the constraints or the eligibility screen." />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Instrument</th>
                <th scope="col">Weight</th>
                <th scope="col">Notional</th>
                <th scope="col">Contracts</th>
                <th scope="col">Side</th>
                <th scope="col">Strategies</th>
              </tr>
            </thead>
            <tbody>
              {proposal.holdings.map((holding) => (
                <tr key={holding.symbol} className="measure-row">
                  <th scope="row"><span className="mono">{holding.symbol}</span></th>
                  <td className="measure-value">{(holding.weight * 100).toFixed(2)}%</td>
                  <td className="measure-value">{money(holding.target_notional)}</td>
                  <td className="measure-value">
                    {holding.contracts === null ? <span className="figure is-absent">no price</span> : holding.contracts}
                  </td>
                  <td className="measure-status">
                    <StatusPill
                      label={holding.weight > 0 ? 'LONG' : 'SHORT'}
                      tone={holding.weight > 0 ? 'good' : 'warn'}
                    />
                  </td>
                  <td className="measure-detail mono">{holding.strategy_ids.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <MeasureTable
        title="Constraints"
        meta={`${proposal.covariance_method} covariance, ${proposal.observations} observations`}
      >
        {proposal.constraints.map((constraint) => (
          <MeasureRow
            key={constraint.name}
            label={constraint.name.replace(/_/g, ' ')}
            observed={constraint.applied ? constraint.observed : null}
            limit={constraint.applied ? constraint.limit : null}
            status={{
              label: constraint.applied ? (constraint.binding ? 'BINDING' : 'SLACK') : 'NOT APPLIED',
              tone: constraint.applied ? (constraint.binding ? 'warn' : 'good') : 'unknown',
            }}
            detail={constraint.detail}
            decimals={4}
          />
        ))}
      </MeasureTable>

      {proposal.excluded.length > 0 && (
        <section className="measure-panel">
          <PanelHead title="Signals that were not sized" meta={`${proposal.excluded.length} excluded`} />
          <ul className="rule-list">
            {proposal.excluded.map((row, index) => (
              <li key={`${row.symbol}-${index}`}>
                <b className="mono">{row.symbol}</b>
                <span>{row.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Limitations items={proposal.limitations} />
    </>
  )
}

// ── risk ─────────────────────────────────────────────────────────────────────

function RiskSection() {
  const risk = useFundRisk()
  if (risk.isPending) return <div className="state" role="status">Measuring the book…</div>
  const assessment = risk.data?.risk
  if (!assessment) return <Empty title="No risk assessment" detail="The risk engine returned nothing." />

  return (
    <div className="fund-view af-panel-in">
      <section className="prop-summary" data-tone={assessment.enabled ? (assessment.within_limits ? 'good' : 'bad') : 'bad'}>
        <div className="prop-verdict">
          <StatusPill
            label={assessment.enabled ? (assessment.within_limits ? 'WITHIN LIMITS' : 'BREACH') : 'HALTED'}
            tone={assessment.enabled && assessment.within_limits ? 'good' : 'bad'}
          />
          <b>{assessment.limits_name}</b>
          <span>
            {!assessment.enabled
              ? 'The kill switch is engaged. Every order is refused until a person re-enables the limit set.'
              : assessment.within_limits
                ? 'No measured quantity exceeds its ceiling.'
                : assessment.breaches.map((breach) => breach.detail).join(' · ')}
          </span>
        </div>
      </section>

      <MeasureTable title="Measures" meta="ceilings are enforceable and AI cannot change them">
        {assessment.measures.map((measure: RiskMeasure) => (
          <MeasureRow
            key={measure.key}
            label={measure.label}
            method={measure.method || undefined}
            observed={measure.value}
            limit={measure.ceiling}
            status={{
              label: measure.value === null ? 'NOT MEASURED'
                : measure.ceiling !== null && measure.value > measure.ceiling ? 'BREACH' : 'OK',
              tone: measure.value === null ? 'unknown'
                : measure.ceiling !== null && measure.value > measure.ceiling ? 'bad' : 'good',
            }}
            headroom={
              measure.value === null || measure.ceiling === null || measure.ceiling === 0
                ? null
                : Math.max(0, 1 - measure.value / measure.ceiling)
            }
            detail={measure.note}
            decimals={4}
          />
        ))}
      </MeasureTable>

      {Object.keys(assessment.by_strategy).length > 0 && (
        <section className="measure-panel">
          <PanelHead title="Exposure by strategy" meta="a position held by two strategies counts in full for each" />
          <ul className="rule-list">
            {Object.entries(assessment.by_strategy).map(([id, weight]) => (
              <li key={id}>
                <b className="mono">{id}</b>
                <span>{ratio(weight)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Limitations items={assessment.limitations} />
    </div>
  )
}

// ── the gate ─────────────────────────────────────────────────────────────────

function GateSection() {
  const screened = useScreenedOrders()
  const { submit } = useFundActions()
  const [chosen, setChosen] = useState<string[]>([])

  const rows = screened.data ?? []
  const cleared = rows.filter((row) => row.decision.decision === 'allow')
  const blocked = rows.filter((row) => row.decision.decision === 'block')

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Screened" value={rows.length} />
        <Stat label="Cleared" value={cleared.length} tone={cleared.length ? 'good' : 'plain'} />
        <Stat label="Blocked" value={blocked.length} tone={blocked.length ? 'bad' : 'plain'} />
      </section>

      {rows.length === 0 ? (
        <NotMeasured
          what="Nothing has been screened"
          why="Construct a portfolio and prepare its rebalance; every order it produces passes through this gate before the OMS will accept it."
          action={<a className="fund-link" href="#portfolio">Open Portfolio</a>}
        />
      ) : (
        <>
          <section className="fund-actions">
            <button
              onClick={() => submit.mutate(chosen)}
              disabled={!chosen.length || submit.isPending}
            >
              {submit.isPending ? 'Submitting…' : `Submit ${chosen.length} cleared order(s)`}
            </button>
            <p>
              Only cleared orders can be selected. The OMS re-checks each one against the clearance
              it carries, so an order edited after screening is refused there too.
            </p>
          </section>
          {submit.isError && <p className="form-error" role="alert">{(submit.error as Error).message}</p>}
          {submit.data && (
            <ul className="rule-list">
              {submit.data.results.map((result) => (
                <li key={result.order_id}>
                  <b className="mono">{result.order_id.slice(0, 14)}</b>
                  <span>{result.accepted ? 'accepted — simulated fill' : result.reason}</span>
                </li>
              ))}
            </ul>
          )}

          {rows.map((row) => (
            <GateCard
              key={row.order.order_id}
              row={row}
              selected={chosen.includes(row.order.order_id)}
              onToggle={() =>
                setChosen((current) =>
                  current.includes(row.order.order_id)
                    ? current.filter((id) => id !== row.order.order_id)
                    : [...current, row.order.order_id],
                )
              }
            />
          ))}
        </>
      )}
    </div>
  )
}

function GateCard({ row, selected, onToggle }: { row: ScreenedOrder; selected: boolean; onToggle: () => void }) {
  const allowed = row.decision.decision === 'allow'
  return (
    <section className="gate-card" data-decision={row.decision.decision}>
      <header>
        <StatusPill label={allowed ? 'CLEARED' : 'BLOCKED'} tone={allowed ? 'good' : 'bad'} />
        <b className="mono">
          {row.order.side.toUpperCase()} {row.order.quantity} {row.order.symbol}
        </b>
        <span className="mono">{row.order.order_id.slice(0, 14)}</span>
        {allowed && (
          <label className="gate-select">
            <input type="checkbox" checked={selected} onChange={onToggle} />
            <span>Select for submission</span>
          </label>
        )}
      </header>
      {!allowed && (
        <ul className="gate-reasons">
          {row.decision.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <ol className="gate-ladder">
        {row.decision.checks.map((check: GateCheck) => (
          <li key={check.check} data-status={check.status}>
            <span className="gate-check">{check.check.replace(/_/g, ' ')}</span>
            <StatusPill
              label={check.status === 'pass' ? 'PASS' : check.status === 'fail' ? 'FAIL' : 'NOT ESTABLISHED'}
              tone={check.status === 'pass' ? 'good' : check.status === 'fail' ? 'bad' : 'unknown'}
            />
            <em>{check.detail}</em>
          </li>
        ))}
      </ol>
    </section>
  )
}

// ── execution and operations ─────────────────────────────────────────────────

function ExecutionSection() {
  const operations = useFundOperations()
  if (operations.isPending) return <div className="state" role="status">Reading the book…</div>
  const data = operations.data!
  const fills = data.fills ?? []

  return (
    <div className="fund-view af-panel-in">
      <p className="fund-note">
        <b>{data.execution_mode}</b> — every fill below came from a local simulator and is labelled
        as such on the record, not only on this screen.
      </p>
      <section className="measure-panel">
        <PanelHead title="Fills" meta={`${fills.length} recorded`} />
        {fills.length === 0 ? (
          <NotMeasured what="No fills" why="Nothing has been submitted through the OMS yet." />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Instrument</th>
                <th scope="col">Side</th>
                <th scope="col">Qty</th>
                <th scope="col">Price</th>
                <th scope="col">How it was priced</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((fill) => (
                <tr key={fill.fill_id} className="measure-row">
                  <th scope="row"><span className="mono">{fill.filled_at.slice(11, 19)}</span></th>
                  <td className="measure-value mono">{fill.symbol}</td>
                  <td className="measure-value">{fill.side.toUpperCase()}</td>
                  <td className="measure-value">{fill.quantity}</td>
                  <td className="measure-value">{fill.price.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
                  <td className="measure-detail">{fill.basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <Limitations items={data.limitations} />
    </div>
  )
}

function OperationsSection() {
  const operations = useFundOperations()
  if (operations.isPending) return <div className="state" role="status">Reconciling…</div>
  const data = operations.data!
  const book = data.book
  const reconciliation = data.reconciliation

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Cash" value={money(book.cash)} />
        <Stat label="Realised" value={money(book.realised_pnl)} tone={book.realised_pnl >= 0 ? 'good' : 'bad'} />
        <Stat label="Commission" value={money(book.commission_paid)} />
        <Stat label="Modelled slippage" value={money(book.slippage_paid)} />
        <Stat label="Open orders" value={book.open_orders.length} />
        <Stat
          label="Reconciliation"
          value={reconciliation.reconciled ? 'CLEAN' : `${reconciliation.discrepancies.length} ISSUE`}
          tone={reconciliation.reconciled ? 'good' : 'bad'}
          note={`${reconciliation.orders} orders, ${reconciliation.fills} fills`}
        />
      </section>

      <section className="measure-panel">
        <PanelHead title="Positions" meta={book.simulated ? 'simulated book' : 'mixed book'} />
        {book.positions.length === 0 ? (
          <NotMeasured what="Flat" why="No position is open in the simulated book." />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Instrument</th>
                <th scope="col">Quantity</th>
                <th scope="col">Average</th>
                <th scope="col">Realised</th>
                <th scope="col" />
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {book.positions.map((position) => (
                <tr key={position.symbol} className="measure-row">
                  <th scope="row"><span className="mono">{position.symbol}</span></th>
                  <td className="measure-value">{position.quantity}</td>
                  <td className="measure-value">{position.average_price.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
                  <td className="measure-value">{money(position.realised_pnl)}</td>
                  <td /><td />
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="measure-panel">
        <PanelHead title="Orders" meta={`${data.orders.length} recorded`} />
        {data.orders.length === 0 ? (
          <NotMeasured what="No orders" why="Nothing has reached the OMS." />
        ) : (
          <ul className="rule-list">
            {data.orders.map((order) => (
              <li key={order.order_id}>
                <b className="mono">
                  {order.side.toUpperCase()} {order.quantity} {order.symbol}
                </b>
                <span>
                  {order.status} · filled {order.filled_quantity}
                  {order.average_price !== null ? ` @ ${order.average_price}` : ''} · {order.venue}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {!reconciliation.reconciled && (
        <section className="measure-panel">
          <PanelHead title="Discrepancies" meta="what was ordered against what came back" />
          <pre className="fund-pre">{JSON.stringify(reconciliation.discrepancies, null, 2)}</pre>
        </section>
      )}

      <Limitations items={data.limitations} />
    </div>
  )
}

// ── performance ──────────────────────────────────────────────────────────────

function PerformanceSection() {
  const performance = useFundPerformance()
  if (performance.isPending) return <div className="state" role="status">Attributing…</div>
  const report = performance.data as {
    statistics: Record<string, number | null | string>
    by_strategy: { key: string; pnl: number; share: number; trades: number }[]
    by_symbol: { key: string; pnl: number; share: number; trades: number }[]
    by_sector: { key: string; pnl: number; share: number; trades: number }[]
    execution: { implementation_shortfall: number; commission: number; modelled_slippage: number; shortfall_per_contract: number | null; simulated: boolean; note: string } | null
    limitations: string[]
  }
  const stats = report.statistics

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Total P&L" value={money(Number(stats.total_pnl ?? 0))} />
        <Stat label="Periods" value={String(stats.periods ?? 0)} />
        <Stat
          label="Sharpe"
          value={stats.sharpe === null ? '—' : Number(stats.sharpe).toFixed(3)}
          tone={stats.sharpe === null ? 'unknown' : 'plain'}
        />
        <Stat
          label="Sortino"
          value={stats.sortino === null ? '—' : Number(stats.sortino).toFixed(3)}
          tone={stats.sortino === null ? 'unknown' : 'plain'}
        />
        <Stat label="Max drawdown" value={money(Number(stats.max_drawdown ?? 0))} />
        <Stat
          label="Hit rate"
          value={stats.hit_rate === null ? '—' : `${(Number(stats.hit_rate) * 100).toFixed(1)}%`}
          tone={stats.hit_rate === null ? 'unknown' : 'plain'}
        />
        <Stat
          label="Profit factor"
          value={stats.profit_factor === null ? '—' : Number(stats.profit_factor).toFixed(2)}
          tone={stats.profit_factor === null ? 'unknown' : 'plain'}
          note={stats.profit_factor === null ? 'no losing period in this sample' : undefined}
        />
      </section>

      {[['By strategy', report.by_strategy], ['By instrument', report.by_symbol], ['By sector', report.by_sector]].map(
        ([title, rows]) => (
          <section className="measure-panel" key={String(title)}>
            <PanelHead title={String(title)} meta="share is of gross contribution" />
            {(rows as typeof report.by_strategy).length === 0 ? (
              <NotMeasured what="Nothing to attribute" why="No realised P&L has been recorded in the book." />
            ) : (
              <ul className="rule-list">
                {(rows as typeof report.by_strategy).map((row) => (
                  <li key={row.key}>
                    <b className="mono">{row.key}</b>
                    <span>
                      {money(row.pnl)} · {(row.share * 100).toFixed(1)}% of gross · {row.trades} line(s)
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ),
      )}

      {report.execution && (
        <section className="measure-panel">
          <PanelHead title="Execution" meta="measured against arrival price" />
          <MeasureTable head={false}>
            <MeasureRow label="Implementation shortfall" observed={report.execution.implementation_shortfall} detail={report.execution.note} />
            <MeasureRow label="Commission" observed={report.execution.commission} />
            <MeasureRow label="Modelled slippage" observed={report.execution.modelled_slippage} />
            <MeasureRow label="Shortfall per contract" observed={report.execution.shortfall_per_contract} decimals={4} />
          </MeasureTable>
        </section>
      )}

      <Limitations items={report.limitations ?? []} />
    </div>
  )
}

// ── approvals ────────────────────────────────────────────────────────────────

function ApprovalsSection() {
  const approvals = useApprovals()
  const decide = useApprovalDecision()
  if (approvals.isPending) return <div className="state" role="status">Reading the queue…</div>
  const pending = approvals.data?.pending ?? []
  const history = approvals.data?.history ?? []

  return (
    <div className="fund-view af-panel-in">
      <section className="measure-panel">
        <PanelHead title="Awaiting a decision" meta={`${pending.length} pending`} />
        {pending.length === 0 ? (
          <NotMeasured
            what="Nothing is waiting"
            why="An assistant's consequential action lands here with the arguments it would have used. Approving runs it as you, now, so it re-validates against current state."
          />
        ) : (
          pending.map((request) => (
            <article className="approval-card" key={request.request_id}>
              <header>
                <b className="mono">{request.action}</b>
                <span>proposed by {request.requested_by}{request.origin ? ` · ${request.origin}` : ''}</span>
                <span className="mono">expires {request.expires_at.slice(11, 19)}</span>
              </header>
              <p>{request.reason}</p>
              <pre className="fund-pre">{JSON.stringify(request.arguments, null, 2)}</pre>
              <div className="approval-actions">
                <button
                  onClick={() => decide.mutate({ id: request.request_id, decision: 'approve' })}
                  disabled={decide.isPending}
                >
                  Approve and run
                </button>
                <button
                  className="is-secondary"
                  onClick={() => decide.mutate({ id: request.request_id, decision: 'reject' })}
                  disabled={decide.isPending}
                >
                  Reject
                </button>
              </div>
            </article>
          ))
        )}
        {decide.isError && <p className="form-error" role="alert">{(decide.error as Error).message}</p>}
      </section>

      <section className="measure-panel">
        <PanelHead title="Decided" meta={`${history.length} in the record`} />
        {history.length === 0 ? (
          <NotMeasured what="Nothing decided yet" why="Decisions are kept so a run can be reconstructed later." />
        ) : (
          <ul className="rule-list">
            {history.map((request) => (
              <li key={request.request_id}>
                <b className="mono">{request.action}</b>
                <span>
                  {request.status}
                  {request.decided_by ? ` by ${request.decided_by}` : ''}
                  {request.error ? ` — ${request.error}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

// ── audit ────────────────────────────────────────────────────────────────────

function AuditSection() {
  const audit = useAudit(200)
  if (audit.isPending) return <div className="state" role="status">Reading the audit trail…</div>
  const entries = audit.data?.entries ?? []
  const summary = audit.data?.summary ?? {}

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        {Object.entries(summary).map(([outcome, count]) => (
          <Stat
            key={outcome}
            label={outcome.replace(/_/g, ' ')}
            value={count}
            tone={outcome === 'denied' || outcome === 'error' ? 'bad' : outcome === 'ok' ? 'good' : 'plain'}
          />
        ))}
      </section>
      <section className="measure-panel">
        <PanelHead title="Every call, allowed or refused" meta={`${entries.length} shown`} />
        {entries.length === 0 ? (
          <NotMeasured what="Nothing recorded" why="The audit trail fills as actions are called." />
        ) : (
          <table className="measure-table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Actor</th>
                <th scope="col">Action</th>
                <th scope="col">Outcome</th>
                <th scope="col">Ruling</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.entry_id} className="measure-row">
                  <th scope="row"><span className="mono">{entry.at.slice(11, 19)}</span></th>
                  <td className="measure-value">
                    {entry.actor}
                    {entry.origin ? <em> · {entry.origin}</em> : null}
                  </td>
                  <td className="measure-value mono">{entry.action}</td>
                  <td className="measure-status">
                    <StatusPill
                      label={entry.outcome.replace(/_/g, ' ').toUpperCase()}
                      tone={
                        entry.outcome === 'ok' ? 'good'
                          : entry.outcome === 'denied' || entry.outcome === 'error' || entry.outcome === 'blocked' ? 'bad'
                            : 'warn'
                      }
                    />
                  </td>
                  <td className="measure-value">{entry.ruling}</td>
                  <td className="measure-detail">{entry.error || entry.ruling_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

// ── formatting ───────────────────────────────────────────────────────────────

const money = (value: number) =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ratio = (value: number) => `${(value * 100).toFixed(1)}%`
