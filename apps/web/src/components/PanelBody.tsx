import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { useApprovals, useAudit, useFundOperations, useFundRisk, useFundState } from '../fund'
import { usePropStatus, LEVEL_LABEL, LEVEL_TONE } from '../prop'
import {
  PERMISSION_LABEL,
  PERMISSION_TONE,
  useAllocation,
  useConnections,
  useDeskActivity,
  useGroups,
  useNews,
  useRisk,
} from '../propdesk'
import { StatusPill } from './measures'
import { ChartPanel, type TimeframeKey } from './PriceChart'
import type { DatasetInfo } from '../types'

/* What goes inside a panel.
 *
 * Some kinds are backed by a real endpoint and show real records. Some are
 * surfaces the product intends to have and does not have yet. The rule is that
 * the second kind must *say so* — a DOM panel drawing plausible depth would be
 * indistinguishable from one wired to a broker, and that is the single most
 * dangerous thing this application could render. So an unbuilt panel says it is
 * unbuilt, and an empty one says it is empty, and neither pretends.
 */

type PanelProps = {
  kind: string
  settings: Record<string, unknown>
  datasets: DatasetInfo[]
  onSetting: (key: string, value: string) => void
}

/** A panel that does not exist yet, labelled as scaffolding rather than dressed up. */
function NotBuilt({ what, why }: { what: string; why: string }) {
  return (
    <div className="panel-state scaffold" role="status">
      <strong>{what} is not built yet</strong>
      <p>{why}</p>
    </div>
  )
}

function Loading() {
  return (
    <div className="panel-state" role="status">
      Loading…
    </div>
  )
}

function Failed({ error }: { error: unknown }) {
  return (
    <div className="panel-state error" role="alert">
      {error instanceof Error ? error.message : 'Request failed'}
    </div>
  )
}

function Empty({ message }: { message: string }) {
  return (
    <div className="panel-state" role="status">
      {message}
    </div>
  )
}

function StrategiesPanel() {
  const query = useQuery({ queryKey: ['strategies'], queryFn: () => getJson<Record<string, unknown>[]>('/strategies') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No strategies have been created yet." />
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Strategy</th><th>Family</th><th className="num">Runs</th></tr>
      </thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.strategy_id)}>
            <td className="mono">{String(row.name ?? row.strategy_id)}</td>
            <td>{String(row.family ?? '—')}</td>
            <td className="num mono">{String(row.backtest_count ?? 0)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Run snapshots: the immutable execution ledger, not backtest results.
 *
 * `/runs` returns what was executed and under which tier — the provenance
 * record — so this shows that rather than P&L. Reading a net figure off this
 * endpoint would have meant inventing one. */
function RunsPanel() {
  const query = useQuery({ queryKey: ['runs'], queryFn: () => getJson<Record<string, unknown>[]>('/runs') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No runs have been recorded yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Run</th><th>Tier</th><th>Status</th></tr></thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.run_id)}>
            <td className="mono">{String(row.run_id ?? '—').slice(0, 22)}</td>
            <td className="mono">{String(row.tier ?? '—')}</td>
            <td>{String(row.status ?? '—')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ActivityPanel() {
  const query = useQuery({
    queryKey: ['activity'],
    queryFn: () => getJson<Record<string, unknown>[]>('/activity'),
    refetchInterval: 5000,
  })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="Nothing has happened yet." />
  return (
    <ul className="panel-log">
      {rows.slice(0, 120).map((row, index) => (
        <li key={index} className={`is-${String(row.level ?? 'info')}`}>
          <span className="stage mono">{String(row.stage ?? '')}</span>
          <span>{String(row.message ?? '')}</span>
        </li>
      ))}
    </ul>
  )
}

function DataHealthPanel({ datasets }: { datasets: DatasetInfo[] }) {
  if (!datasets.length) return <Empty message="No datasets are registered." />
  return (
    <table className="panel-table">
      <thead><tr><th>Dataset</th><th className="num">Bars</th><th>Source</th><th>State</th></tr></thead>
      <tbody>
        {datasets.map((row) => (
          <tr key={row.key}>
            <td className="mono">{row.label}</td>
            <td className="num mono">{row.bar_count ? row.bar_count.toLocaleString() : '—'}</td>
            <td>{row.provider}</td>
            {/* Three distinct states, never collapsed into a tick. */}
            <td className={row.available ? 'ok' : 'missing'}>
              {row.available ? (row.is_real ? 'available' : 'fixture') : 'not present'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function MemoryPanel() {
  const query = useQuery({ queryKey: ['memory'], queryFn: () => getJson<{ counts: Record<string, number>; total: number }>('/memory') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const counts = query.data?.counts ?? {}
  const entries = Object.entries(counts)
  if (!entries.length) return <Empty message="Research memory is empty — nothing has failed yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Failure class</th><th className="num">Count</th></tr></thead>
      <tbody>
        {entries.sort((a, b) => b[1] - a[1]).map(([name, count]) => (
          <tr key={name}><td className="mono">{name}</td><td className="num mono">{count}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function ExperimentsPanel() {
  const query = useQuery({ queryKey: ['experiments'], queryFn: () => getJson<Record<string, unknown>[]>('/experiments') })
  if (query.isPending) return <Loading />
  if (query.isError) return <Failed error={query.error} />
  const rows = query.data ?? []
  if (!rows.length) return <Empty message="No experiments have been recorded yet." />
  return (
    <table className="panel-table">
      <thead><tr><th>Experiment</th><th>Status</th></tr></thead>
      <tbody>
        {rows.slice(0, 200).map((row) => (
          <tr key={String(row.id)}>
            <td className="mono">{String(row.template ?? row.id)}</td>
            <td>{String(row.status ?? '—')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function WatchlistPanel({ settings, onSetting }: { settings: Record<string, unknown>; onSetting: (k: string, v: string) => void }) {
  // Stored on the panel itself, so it travels with the workspace and survives a
  // restart through the same store as everything else on the layout.
  const symbols = String(settings.symbols ?? '')
    .split(',')
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)

  return (
    <div className="panel-watchlist">
      <label className="watchlist-input">
        <span className="sr-only">Watchlist symbols, comma separated</span>
        <input
          aria-label="Watchlist symbols"
          defaultValue={symbols.join(', ')}
          placeholder="NQ, ES, GC"
          onBlur={(event) => onSetting('symbols', event.target.value)}
        />
      </label>
      {symbols.length === 0 ? (
        <Empty message="No symbols yet. Type them above." />
      ) : (
        <ul className="panel-symbols">
          {symbols.map((symbol) => (
            <li key={symbol} className="mono">
              {symbol}
              {/* No price: this build has no quote stream, and a number here
                  would be invented. */}
              <span className="no-quote">no live quote</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


/* ── the fund and the account, at panel scale ─────────────────────────────
 *
 * These read the same endpoints the full screens do. A panel is a smaller view
 * of one truth, not a summary computed separately — a workspace panel that
 * disagreed with the screen it links to would be worse than no panel.
 *
 * The execution surfaces below are no longer scaffolding. They were, correctly,
 * while there was no book: this build now has a simulated one, and what they
 * draw is labelled simulated on the record rather than only in a caption. */

function FundSummaryPanel() {
  const state = useFundState()
  if (state.isPending) return <Loading />
  if (state.isError) return <Failed error={state.error} />
  const fund = state.data!
  return (
    <div className="panel-fund">
      <div className="panel-figures">
        <span><em>NAV</em><b>{fund.nav.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b></span>
        <span><em>Gross</em><b>{(fund.gross_exposure * 100).toFixed(0)}%</b></span>
        <span><em>Net</em><b>{(fund.net_exposure * 100).toFixed(0)}%</b></span>
        <span><em>Leverage</em><b>{fund.leverage.toFixed(2)}×</b></span>
        <span>
          <em>Risk</em>
          <StatusPill
            label={fund.risk.enabled ? (fund.risk.within_limits ? 'WITHIN' : 'BREACH') : 'HALTED'}
            tone={fund.risk.enabled && fund.risk.within_limits ? 'good' : 'bad'}
          />
        </span>
        <span><em>Execution</em><b>{fund.execution_mode}</b></span>
      </div>
      <ol className="panel-loop">
        {fund.stages.map((stage) => (
          <li key={stage.stage} data-status={stage.status}>
            <a href={`#${stage.route}`}>{stage.label}</a>
            <span>{stage.summary}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

function PortfolioPanel() {
  const operations = useFundOperations()
  if (operations.isPending) return <Loading />
  if (operations.isError) return <Failed error={operations.error} />
  const positions = operations.data!.book.positions
  if (!positions.length) {
    return <Empty message="The book is flat. Construct a portfolio on the Portfolio screen to propose one." />
  }
  return (
    <table className="panel-table">
      <tbody>
        {positions.map((position) => (
          <tr key={position.symbol}>
            <td className="mono">{position.symbol}</td>
            <td>{position.quantity}</td>
            <td className="mono">{position.average_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PretradeGatePanel() {
  const screened = useQuery({
    queryKey: ['fund-screened'],
    queryFn: () => getJson<{ decision: { order_id: string; symbol: string; decision: string; reasons: string[] } }[]>('/fund/orders/screened'),
  })
  if (screened.isPending) return <Loading />
  if (screened.isError) return <Failed error={screened.error} />
  const rows = screened.data ?? []
  if (!rows.length) return <Empty message="Nothing has been screened. Prepare a rebalance to fill this." />
  return (
    <ul className="panel-list">
      {rows.map((row) => (
        <li key={row.decision.order_id}>
          <StatusPill
            label={row.decision.decision === 'allow' ? 'CLEARED' : 'BLOCKED'}
            tone={row.decision.decision === 'allow' ? 'good' : 'bad'}
          />
          <span className="mono">{row.decision.symbol}</span>
          <em>{row.decision.reasons[0] ?? 'every check passed'}</em>
        </li>
      ))}
    </ul>
  )
}

function ApprovalsPanel() {
  const approvals = useApprovals()
  if (approvals.isPending) return <Loading />
  if (approvals.isError) return <Failed error={approvals.error} />
  const pending = approvals.data?.pending ?? []
  if (!pending.length) return <Empty message="Nothing is waiting for you." />
  return (
    <ul className="panel-list">
      {pending.map((request) => (
        <li key={request.request_id}>
          <StatusPill label="HELD" tone="warn" />
          <span className="mono">{request.action}</span>
          <em>{request.reason}</em>
        </li>
      ))}
    </ul>
  )
}

function AuditPanel() {
  const audit = useAudit(40)
  if (audit.isPending) return <Loading />
  if (audit.isError) return <Failed error={audit.error} />
  const entries = audit.data?.entries ?? []
  if (!entries.length) return <Empty message="No action has been recorded yet." />
  return (
    <ul className="panel-list">
      {entries.map((entry) => (
        <li key={entry.entry_id}>
          <time className="mono">{entry.at.slice(11, 19)}</time>
          <span className="mono">{entry.action}</span>
          <em>{entry.actor === 'ai' ? 'assistant' : 'you'} · {entry.outcome.replace(/_/g, ' ')}</em>
        </li>
      ))}
    </ul>
  )
}

function PropPanel() {
  const status = usePropStatus()
  if (status.isPending) return <Loading />
  if (status.isError) return <Failed error={status.error} />
  const assessment = status.data?.assessment
  if (!assessment) {
    return <Empty message={status.data?.reason ?? 'No account is configured. Prop Firm mode evaluates a rule set you supply.'} />
  }
  return (
    <div className="panel-fund">
      <div className="panel-figures">
        <span><em>Equity</em><b>{assessment.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b></span>
        <span><em>Floor</em><b>{assessment.loss_floor.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b></span>
        <span><em>Buffer</em><b>{(assessment.equity - assessment.loss_floor).toLocaleString(undefined, { maximumFractionDigits: 0 })}</b></span>
        <span>
          <em>Status</em>
          <StatusPill label={LEVEL_LABEL[assessment.level]} tone={LEVEL_TONE[assessment.level]} />
        </span>
      </div>
      <ul className="panel-list">
        {assessment.statuses.filter((rule) => rule.level !== 'not_assessed').map((rule) => (
          <li key={rule.key}>
            <StatusPill label={LEVEL_LABEL[rule.level]} tone={LEVEL_TONE[rule.level]} />
            <span>{rule.label}</span>
            <em>{rule.detail}</em>
          </li>
        ))}
      </ul>
    </div>
  )
}

function RiskPanel() {
  const risk = useFundRisk()
  if (risk.isPending) return <Loading />
  if (risk.isError) return <Failed error={risk.error} />
  const assessment = risk.data?.risk
  if (!assessment) return <Empty message="The risk engine returned nothing." />
  return (
    <ul className="panel-list">
      {assessment.measures.map((measure) => (
        <li key={measure.key}>
          <StatusPill
            label={
              measure.value === null ? 'NOT MEASURED'
                : measure.ceiling !== null && measure.value > measure.ceiling ? 'BREACH' : 'OK'
            }
            tone={
              measure.value === null ? 'unknown'
                : measure.ceiling !== null && measure.value > measure.ceiling ? 'bad' : 'good'
            }
          />
          <span>{measure.label}</span>
          <em className="mono">
            {measure.value === null ? measure.note : measure.value.toFixed(4)}
            {measure.ceiling !== null && measure.value !== null ? ` / ${measure.ceiling}` : ''}
          </em>
        </li>
      ))}
    </ul>
  )
}

function BookPanel({ what }: { what: 'positions' | 'orders' | 'account' }) {
  const operations = useFundOperations()
  if (operations.isPending) return <Loading />
  if (operations.isError) return <Failed error={operations.error} />
  const data = operations.data!
  if (what === 'account') {
    return (
      <div className="panel-figures">
        <span><em>Cash</em><b>{data.book.cash.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></span>
        <span><em>Realised</em><b>{data.book.realised_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></span>
        <span><em>Commission</em><b>{data.book.commission_paid.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></span>
        <span><em>Venue</em><b>{data.book.venues.join(', ') || 'none yet'}</b></span>
        <span><em>Fills</em><b>{data.book.simulated ? 'SIMULATED' : 'MIXED'}</b></span>
      </div>
    )
  }
  if (what === 'positions') {
    if (!data.book.positions.length) return <Empty message="Flat. No position is open in the simulated book." />
    return (
      <table className="panel-table">
        <tbody>
          {data.book.positions.map((position) => (
            <tr key={position.symbol}>
              <td className="mono">{position.symbol}</td>
              <td>{position.quantity}</td>
              <td className="mono">{position.average_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }
  if (!data.orders.length) return <Empty message="No order has reached the OMS." />
  return (
    <table className="panel-table">
      <tbody>
        {data.orders.map((order) => (
          <tr key={order.order_id}>
            <td className="mono">{order.symbol}</td>
            <td>{order.side.toUpperCase()} {order.quantity}</td>
            <td>{order.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}


/* ── the Prop Desk ────────────────────────────────────────────────────────────
 *
 * Small, dense versions of what the Prop Desk screens show, so an operator can
 * put their accounts next to a chart. Each one is backed by the same endpoint
 * the full screen reads; none of them invents a number, and an account whose
 * provider has reported no balance says so rather than showing a zero.
 */

function DeskAccountsPanel() {
  const connections = useConnections()
  if (connections.isPending) return <Loading />
  if (connections.error) return <Failed error={connections.error} />
  const rows = connections.data?.accounts ?? []
  const links = connections.data?.connections ?? []
  if (rows.length === 0) {
    return (
      <Empty message="No accounts. Only the simulator executes in this build; a real provider's accounts are discovered from the provider." />
    )
  }
  const state = new Map(links.map((item) => [item.connection_id, item.state]))
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Account</th><th>Connection</th><th>Equity</th><th>Rules</th></tr>
      </thead>
      <tbody>
        {rows.map((account) => (
          <tr key={account.account_uid}>
            <td>{account.display_name || account.account_uid}</td>
            <td>{state.get(account.connection_id) ?? 'unknown'}</td>
            <td>{account.equity == null ? 'not reported' : account.equity.toLocaleString()}</td>
            <td>{account.prop_account_id ? 'linked' : 'none linked'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeskCopyPanel() {
  const groups = useGroups()
  if (groups.isPending) return <Loading />
  if (groups.error) return <Failed error={groups.error} />
  const rows = groups.data?.groups ?? []
  if (rows.length === 0) {
    return <Empty message="No copy groups. A group needs an attestation that every account in it belongs to one owner." />
  }
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Group</th><th>State</th><th>Leader</th><th>Followers</th></tr>
      </thead>
      <tbody>
        {rows.map((group) => (
          <tr key={group.group_id}>
            <td>{group.name}</td>
            <td>
              <StatusPill tone={group.active ? 'good' : 'unknown'} label={group.active ? 'replicating' : 'stopped'} />
            </td>
            <td>{group.leader.account_uid}</td>
            <td>{group.followers.filter((f) => f.active).length} of {group.followers.length}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeskAllocationPanel() {
  const allocation = useAllocation()
  if (allocation.isPending) return <Loading />
  if (allocation.error) return <Failed error={allocation.error} />
  const rows = allocation.data?.allocations ?? []
  if (rows.length === 0) {
    return (
      <Empty message="No account is running an allocated strategy. An allocation needs a strategy the judge passed and an out-of-sample drawdown estimate to size against." />
    )
  }
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Account</th><th>Strategy</th><th>Contracts</th><th>Source</th></tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.account_uid}>
            <td>{row.account_uid}</td>
            <td>{row.strategy_id}</td>
            <td>{row.contracts}</td>
            <td>
              <StatusPill
                tone={row.actionable ? 'good' : 'warn'}
                label={row.actionable ? row.source : 'needs confirmation'}
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeskNewsPanel() {
  const news = useNews(7)
  if (news.isPending) return <Loading />
  if (news.error) return <Failed error={news.error} />
  const state = news.data
  if (!state) return <Empty message="The calendar could not be read." />
  if (state.events.length === 0) {
    return (
      <Empty message="No scheduled events. Record them by hand, or set FRED_API_KEY to read the Federal Reserve Bank of St. Louis release calendar." />
    )
  }
  return (
    <>
      {state.assessment.restricted && (
        <p className="panel-state" role="status">
          Inside a blackout window: {state.assessment.events.map((e) => e.title).join(', ')}
        </p>
      )}
      <table className="panel-table">
        <thead><tr><th>When</th><th>Event</th><th>Impact</th></tr></thead>
        <tbody>
          {state.events.slice(0, 20).map((event) => (
            <tr key={event.event_id}>
              <td>{new Date(event.at).toLocaleString()}</td>
              <td>{event.title}</td>
              <td>
                <StatusPill
                  tone={event.impact === 'high' ? 'bad' : event.impact === 'unknown' ? 'unknown' : 'warn'}
                  label={event.impact}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function DeskRiskPanel() {
  const risk = useRisk()
  if (risk.isPending) return <Loading />
  if (risk.error) return <Failed error={risk.error} />
  const rows = risk.data?.accounts ?? []
  if (rows.length === 0) return <Empty message="No accounts are connected." />
  const configured = rows.filter((row) => row.settings)
  if (configured.length === 0) {
    return <Empty message="No account has a risk mode yet. Until one is set, nothing can size a position." />
  }
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Account</th><th>Mode</th><th>Risk</th><th>Size</th></tr>
      </thead>
      <tbody>
        {configured.map((row) => (
          <tr key={row.account_uid}>
            <td>{row.account_uid}</td>
            <td>{row.settings!.mode.replace(/_/g, ' ')}</td>
            <td className="num">
              {row.state ? `${(row.state.current_fraction * 100).toFixed(2)}%` : '—'}
            </td>
            <td className="num">
              {row.last_proposal?.contracts_after ?? (
                <StatusPill tone="unknown" label="Not measured" />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeskAiPanel() {
  const risk = useRisk()
  if (risk.isPending) return <Loading />
  if (risk.error) return <Failed error={risk.error} />
  const rows = risk.data?.accounts ?? []
  const managed = rows.filter((row) => row.settings?.mode === 'ai_managed')
  const autonomous = rows.filter((row) => row.autonomy !== 'off')
  if (managed.length === 0 && autonomous.length === 0) {
    return <Empty message="AI manages nothing. No account is on AI risk management and autonomous deployment is off everywhere." />
  }
  return (
    <table className="panel-table">
      <thead>
        <tr><th>Account</th><th>AI risk</th><th>Deployment</th></tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.account_uid}>
            <td>{row.account_uid}</td>
            <td>
              <StatusPill
                tone={row.settings?.mode === 'ai_managed' ? 'warn' : 'plain'}
                label={row.settings?.mode === 'ai_managed' ? 'Managed' : 'Off'}
              />
            </td>
            <td>
              <StatusPill
                tone={row.autonomy === 'off' ? 'plain' : 'warn'}
                label={row.autonomy.replace(/_/g, ' ')}
              />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DeskActivityPanel() {
  const activity = useDeskActivity(50)
  if (activity.isPending) return <Loading />
  if (activity.error) return <Failed error={activity.error} />
  const rows = activity.data?.decisions ?? []
  if (rows.length === 0) return <Empty message="Nothing has been screened yet." />
  return (
    <table className="panel-table">
      <thead>
        <tr><th>When</th><th>Order</th><th>Outcome</th><th>Refused at</th></tr>
      </thead>
      <tbody>
        {rows.map((decision) => (
          <tr key={decision.decision_id}>
            <td>{new Date(decision.at).toLocaleTimeString()}</td>
            <td>
              {decision.intent.side} {decision.intent.quantity} {decision.intent.symbol}
            </td>
            <td>
              <StatusPill
                tone={decision.cleared ? 'good' : 'bad'}
                label={decision.cleared ? 'cleared' : 'refused'}
              />
            </td>
            <td>{decision.blocking_stages.join(', ') || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function PanelBody({ kind, settings, datasets, onSetting }: PanelProps) {
  switch (kind) {
    case 'chart': {
      // A panel can name either a dataset or a symbol, and templates name the
      // symbol because that is what a person says. Resolving symbol -> dataset
      // here is what stops an "ES" panel quietly drawing NQ because it fell
      // through to the first available archive.
      const wanted = String(settings.symbol ?? '').toUpperCase()
      const bySymbol = wanted
        ? datasets.find((d) => d.symbol.toUpperCase() === wanted && d.available)
        : undefined
      const dataset =
        String(settings.dataset ?? '') ||
        bySymbol?.key ||
        datasets.find((d) => d.available && d.is_real)?.key ||
        datasets[0]?.key ||
        ''
      if (wanted && !bySymbol && !settings.dataset) {
        return (
          <Empty
            message={`No archive for ${wanted}. Add one, or pick a different symbol above.`}
          />
        )
      }
      if (!dataset) return <Empty message="No datasets are registered." />
      return (
        <ChartPanel
          dataset={dataset}
          timeframe={(String(settings.timeframe ?? '1h') as TimeframeKey) || '1h'}
          onDataset={(key) => onSetting('dataset', key)}
          onTimeframe={(key) => onSetting('timeframe', key)}
          datasets={datasets.map((d) => ({
            key: d.key,
            label: d.label,
            available: d.available,
            is_real: d.is_real,
          }))}
          height={0}
        />
      )
    }
    case 'strategies':
      return <StrategiesPanel />
    case 'runs':
      return <RunsPanel />
    case 'experiments':
      return <ExperimentsPanel />
    case 'activity':
    case 'logs':
      return <ActivityPanel />
    case 'data_health':
      return <DataHealthPanel datasets={datasets} />
    case 'research_memory':
      return <MemoryPanel />
    case 'watchlist':
      return <WatchlistPanel settings={settings} onSetting={onSetting} />

    // The simulated book. These were inert while there was no book at all;
    // there is one now, and everything drawn here comes from fills a local
    // simulator produced and labelled as such.
    case 'positions':
      return <BookPanel what="positions" />
    case 'orders':
      return <BookPanel what="orders" />
    case 'account':
      return <BookPanel what="account" />
    case 'risk':
      return <RiskPanel />
    case 'prop':
      return <PropPanel />
    case 'desk_accounts':
      return <DeskAccountsPanel />
    case 'desk_copy':
      return <DeskCopyPanel />
    case 'desk_allocation':
      return <DeskAllocationPanel />
    case 'desk_news':
      return <DeskNewsPanel />
    case 'desk_activity':
      return <DeskActivityPanel />
    case 'desk_risk':
      return <DeskRiskPanel />
    case 'desk_ai':
      return <DeskAiPanel />
    case 'fund_summary':
      return <FundSummaryPanel />
    case 'portfolio':
      return <PortfolioPanel />
    case 'pretrade_gate':
      return <PretradeGatePanel />
    case 'approvals':
      return <ApprovalsPanel />
    case 'audit':
      return <AuditPanel />

    // Still genuinely absent. Depth and an order ticket need a live quote and a
    // venue, and this build has neither — so they say so rather than drawing
    // something that would be indistinguishable from a connected panel.
    case 'dom':
    case 'order_ticket':
      return (
        <NotBuilt
          what={kind.replace('_', ' ')}
          why="Depth and order entry need a live quote stream and a venue. AlgoForge is paper-only and has neither, so nothing is drawn here rather than something that would look connected."
        />
      )
    case 'replay':
      return (
        <NotBuilt
          what="Replay"
          why="The bars and the aggregation are in place; the playback controls are not written yet."
        />
      )
    default:
      return (
        <NotBuilt
          what={kind.replace('_', ' ')}
          why="This panel kind is registered but has no view yet."
        />
      )
  }
}
