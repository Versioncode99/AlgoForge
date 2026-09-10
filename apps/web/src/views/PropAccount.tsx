import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { Limitations, MeasureRow, MeasureTable, NotMeasured, StatusPill } from '../components/measures'
import { Empty, PanelHead, Stat } from '../components/ui'
import {
  LEVEL_LABEL, LEVEL_TONE, type AccountAssessment, type RuleStatus,
  usePropAccounts, usePropMutations, usePropStatus,
} from '../prop'
import type { StrategyListItem } from '../types'

/* Prop Firm mode: one question, answered before anything else.
 *
 * How close am I to breaching. Everything on this screen is arranged around
 * that, and the arrangement changes per section rather than the content: the
 * Drawdown section is the same assessment with the floor rule at the top and
 * the rest below, because a trader who navigated to Drawdown wants the floor
 * first and still needs to see that the daily limit is nearly gone.
 *
 * Two things this screen refuses to do. It does not invent an account state:
 * with nothing recorded it says so, rather than drawing a comfortable buffer
 * against a starting balance that may not be current. And it does not soften a
 * breach — `can_trade` false is stated in the summary, in the tone, and in the
 * words.
 */

type SectionKey = 'account' | 'rules' | 'drawdown' | 'daily' | 'target' | 'risk'

const FOCUS: Record<SectionKey, string[]> = {
  account: [],
  rules: [],
  drawdown: ['max_drawdown'],
  daily: ['daily_loss'],
  target: ['profit_target', 'consistency', 'trading_days'],
  risk: ['position_limit', 'order_limit', 'open_positions', 'risk_per_trade', 'session'],
}

const TITLE: Record<SectionKey, string> = {
  account: 'Account Status',
  rules: 'Rules',
  drawdown: 'Drawdown',
  daily: 'Daily Loss',
  target: 'Profit Target',
  risk: 'Risk',
}

export function PropAccountView({ section }: { section: SectionKey }) {
  const accounts = usePropAccounts()
  const status = usePropStatus()
  const mutations = usePropMutations()

  const list = accounts.data?.accounts ?? []
  if (accounts.isPending || status.isPending) {
    return <div className="state" role="status">Reading the account…</div>
  }
  if (!list.length) {
    return <NoAccount />
  }

  const account = status.data?.account
  const assessment = status.data?.assessment ?? null

  return (
    <div className="prop-view af-panel-in">
      <header className="prop-head">
        <div>
          <p className="eyebrow">{account?.rules.phase === 'FUNDED' ? 'Funded account' : 'Evaluation'}</p>
          <h1>{TITLE[section]}</h1>
        </div>
        {list.length > 1 && (
          <label className="prop-picker">
            <span className="sr-only">Account</span>
            <select
              value={accounts.data?.selected ?? ''}
              onChange={(event) => mutations.select.mutate(event.target.value)}
            >
              {list.map((item) => (
                <option key={item.account_id} value={item.account_id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      {assessment === null ? (
        <NoState reason={status.data?.reason ?? 'no state has been recorded'} accountId={account?.account_id} />
      ) : section === 'rules' ? (
        <RulesSection status={status.data!} />
      ) : (
        <Assessment assessment={assessment} focus={FOCUS[section]} section={section} />
      )}

      <Replay accountId={account?.account_id} />
    </div>
  )
}

function Assessment({
  assessment,
  focus,
  section,
}: {
  assessment: AccountAssessment
  focus: string[]
  section: SectionKey
}) {
  // Focused rules first, then everything else. Not *only* the focused ones: a
  // trader looking at Drawdown whose daily limit is one tick away needs to see
  // it without navigating, and hiding it would be the failure this whole mode
  // exists to prevent.
  const focused = focus.length
    ? assessment.statuses.filter((rule) => focus.includes(rule.key))
    : assessment.statuses
  const rest = focus.length ? assessment.statuses.filter((rule) => !focus.includes(rule.key)) : []

  return (
    <>
      <section className="prop-summary" data-tone={LEVEL_TONE[assessment.level]}>
        <div className="prop-verdict">
          <StatusPill label={LEVEL_LABEL[assessment.level]} tone={LEVEL_TONE[assessment.level]} />
          <b>{assessment.can_trade ? 'Trading permitted' : 'Trading not permitted'}</b>
          <span>
            {assessment.can_trade
              ? assessment.breaches.length
                ? `${assessment.breaches.length} advisory rule(s) unmet — see below.`
                : 'No rule is breached on the recorded state.'
              : `Breached: ${assessment.breaches.join(', ')}.`}
          </span>
        </div>
        <div className="stat-row">
          <Stat label="Balance" value={assessment.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} note="settled" />
          <Stat label="Equity" value={assessment.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} note="balance plus open profit" />
          <Stat
            label="Loss floor"
            value={assessment.loss_floor.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            note="the account ends here"
            tone="bad"
          />
          <Stat
            label="Buffer to floor"
            value={(assessment.equity - assessment.loss_floor).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            note="equity above the floor"
            tone={assessment.equity - assessment.loss_floor > 0 ? 'good' : 'bad'}
          />
        </div>
      </section>

      <MeasureTable
        title={section === 'account' ? 'Every rule' : `${TITLE[section]} and what it depends on`}
        meta={`as of ${assessment.as_of.slice(0, 19).replace('T', ' ')}`}
      >
        {focused.map((rule) => (
          <Rule key={rule.key} rule={rule} />
        ))}
        {rest.length > 0 && (
          <tr className="measure-divider">
            <td colSpan={6}>Everything else, because a rule you are not looking at can still end the account</td>
          </tr>
        )}
        {rest.map((rule) => (
          <Rule key={rule.key} rule={rule} />
        ))}
      </MeasureTable>

      <Limitations
        items={[
          ...assessment.limitations,
          'This evaluates the rule set you configured. AlgoForge asserts nothing about what any named firm’s live contract says.',
          'The assessment is only as current as the state recorded against this account.',
        ]}
      />
    </>
  )
}

function Rule({ rule }: { rule: RuleStatus }) {
  return (
    <MeasureRow
      label={rule.label}
      status={{ label: LEVEL_LABEL[rule.level], tone: LEVEL_TONE[rule.level] }}
      observed={rule.observed}
      limit={rule.limit}
      headroom={rule.headroom}
      detail={rule.detail}
      decimals={typeof rule.observed === 'number' && Math.abs(rule.observed) < 10 ? 4 : 2}
    />
  )
}

function RulesSection({ status }: { status: { account: { rules: Record<string, unknown>; name: string } } }) {
  const rules = status.account.rules as Record<string, unknown>
  const rows: [string, unknown][] = [
    ['Provider label', rules.provider || '—'],
    ['Phase', rules.phase],
    ['Starting balance', rules.starting_balance],
    ['Maximum loss', rules.maximum_loss],
    ['Trailing', String(rules.trail_mode).replace(/_/g, ' ')],
    ['Floor cap', rules.floor_cap ?? 'none — the floor never stops rising'],
    ['Daily loss limit', rules.daily_loss_limit ?? 'not configured'],
    ['Profit target', rules.profit_target ?? 'not configured'],
    ['Position limit', rules.max_position_contracts ?? 'not configured'],
    ['Per-order cap', rules.max_order_contracts ?? 'not configured'],
    ['Concurrent positions', rules.max_open_positions ?? 'not configured'],
    ['Minimum trading days', rules.minimum_trading_days ?? 'not configured'],
    ['Consistency cap', rules.consistency_share ?? 'not configured'],
    ['Per-trade risk cap', rules.max_risk_per_trade ?? 'not configured'],
    ['Timezone', rules.timezone],
  ]
  const windows = (rules.session_windows as { label: string; opens: string; closes: string }[]) ?? []
  const custom = (rules.custom_limits as { key: string; label: string; metric: string; comparison: string; value: number }[]) ?? []

  return (
    <>
      <section className="measure-panel">
        <PanelHead title="The contract this account is held to" meta={String(rules.name)} />
        <table className="measure-table is-plain">
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label} className="measure-row">
                <th scope="row"><span>{label}</span></th>
                <td className="measure-value" colSpan={5}>
                  {typeof value === 'number'
                    ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
                    : String(value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="measure-panel">
        <PanelHead title="Session windows" meta={windows.length ? `${windows.length} configured` : 'none'} />
        {windows.length === 0 ? (
          <NotMeasured
            what="No session restriction"
            why="This account may be traded at any hour, as far as the configured rules are concerned."
          />
        ) : (
          <ul className="rule-list">
            {windows.map((window) => (
              <li key={`${window.opens}-${window.closes}`}>
                <b>{window.label || 'Session'}</b>
                <span className="mono">
                  {window.opens.slice(0, 5)}–{window.closes.slice(0, 5)} {String(rules.timezone)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="measure-panel">
        <PanelHead title="Firm-specific limits" meta={custom.length ? `${custom.length} configured` : 'none'} />
        {custom.length === 0 ? (
          <NotMeasured
            what="No additional limits"
            why="A firm's own constraint can be added as a limit over any metric the account state measures — a rule over something nobody observes could never be checked, so those cannot be entered."
          />
        ) : (
          <ul className="rule-list">
            {custom.map((limit) => (
              <li key={limit.key}>
                <b>{limit.label}</b>
                <span className="mono">
                  {limit.metric} {limit.comparison === 'max' ? '≤' : '≥'} {limit.value}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {Boolean(rules.source_note) && (
        <Limitations items={[String(rules.source_note)]} title="Where this rule set came from" />
      )}
    </>
  )
}

function NoAccount() {
  return (
    <div className="prop-view af-panel-in">
      <Empty
        title="No account is configured"
        detail={
          'Prop Firm mode evaluates a rule set you supply. Nothing here is a copy of any firm’s ' +
          'contract, and no rules are assumed: configure an account with its starting balance, its ' +
          'loss limits and its target, and every screen in this mode is measured against it.'
        }
      />
      <Limitations
        title="Why there is no default"
        items={[
          'Funded-account contracts differ in ways that change the answer — a trailing floor that ratchets intraday is materially harsher than one that settles daily.',
          'Shipping one firm’s numbers as a starting point would mean every account was measured against a contract nobody had read.',
        ]}
      />
    </div>
  )
}

function NoState({ reason, accountId }: { reason: string; accountId?: string }) {
  const mutations = usePropMutations()
  const [balance, setBalance] = useState('')
  const [equity, setEquity] = useState('')

  const record = () => {
    if (!accountId) return
    const settled = Number(balance)
    const marked = equity === '' ? settled : Number(equity)
    const now = new Date().toISOString()
    mutations.record.mutate({
      id: accountId,
      state: {
        as_of: now,
        balance: settled,
        equity: marked,
        // The high-water marks default to the larger of the two figures the
        // operator gave. That is the smallest honest assumption available:
        // taking a lower one would put the trailing floor further away than it
        // is, which is the direction that gets an account closed.
        high_water_balance: Math.max(settled, marked),
        high_water_equity: Math.max(settled, marked),
      },
      source: 'entered by the operator',
    })
  }

  return (
    <>
      <NotMeasured
        what="Nothing has been recorded for this account"
        why={`${reason} An account nobody has recorded is not an account sitting flat at its starting balance, so nothing is drawn against one.`}
      />
      <section className="measure-panel">
        <PanelHead title="Record where the account stands" meta="stored with its source and timestamp" />
        <div className="prop-record">
          <label>
            <span>Settled balance</span>
            <input inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} />
          </label>
          <label>
            <span>Equity</span>
            <input
              inputMode="decimal"
              value={equity}
              placeholder="same as balance when flat"
              onChange={(e) => setEquity(e.target.value)}
            />
          </label>
          <button onClick={record} disabled={!balance || mutations.record.isPending}>
            {mutations.record.isPending ? 'Recording…' : 'Record'}
          </button>
        </div>
        {mutations.record.isError && (
          <p className="form-error" role="alert">{(mutations.record.error as Error).message}</p>
        )}
      </section>
    </>
  )
}

function Replay({ accountId }: { accountId?: string }) {
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => getJson<StrategyListItem[]>('/strategies'),
  })
  const { replay } = usePropMutations()
  const [chosen, setChosen] = useState('')
  const tested = (strategies.data ?? []).filter((item) => item.latest)

  return (
    <section className="measure-panel">
      <PanelHead
        title="Would a strategy have survived this contract?"
        meta="replays its backtested trades through the rule engine"
      />
      {tested.length === 0 ? (
        <NotMeasured
          what="No backtested strategy to replay"
          why="A replay needs closed trades. Build and backtest a strategy, and its trade sequence can be run through these rules day by day."
        />
      ) : (
        <>
          <div className="prop-record">
            <label>
              <span>Strategy</span>
              <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
                <option value="">Choose a strategy</option>
                {tested.map((item) => (
                  <option key={item.strategy_id} value={item.strategy_id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => replay.mutate({ strategyId: chosen, accountId })}
              disabled={!chosen || replay.isPending}
            >
              {replay.isPending ? 'Replaying…' : 'Replay'}
            </button>
          </div>
          {replay.isError && <p className="form-error" role="alert">{(replay.error as Error).message}</p>}
          {replay.data && (
            <>
              <MeasureTable>
                {replay.data.assessment.statuses.map((rule) => (
                  <Rule key={rule.key} rule={rule} />
                ))}
              </MeasureTable>
              <Limitations items={replay.data.limitations} />
            </>
          )}
        </>
      )}
    </section>
  )
}
