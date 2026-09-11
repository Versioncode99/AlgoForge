import { useState } from 'react'
import { Empty, PanelHead, Stat } from '../components/ui'
import { Limitations, NotMeasured, StatusPill } from '../components/measures'
import {
  PERMISSION_LABEL,
  PERMISSION_TONE,
  type AllocationRow,
  type CopyDecision,
  type DeskDecision,
  type Permission,
  useAllocation,
  useConnections,
  useDeskActivity,
  useDeskMutations,
  useGroups,
  useNews,
  usePolicies,
  useProviders,
} from '../propdesk'

/* The Prop Desk: many accounts, one set of deterministic controls.
 *
 * Four things this screen refuses to do, and they are the reason it looks the
 * way it does.
 *
 * It does not draw a provider as connected when there is no connector. The
 * Providers section states, per provider, whether a live connector exists in
 * this build — three of the four say no, and say what it would take.
 *
 * It does not render an unrecorded firm permission as permissive. `unknown`
 * gets a warning tone and the words "not recorded", because a rule nobody has
 * read is a hazard rather than the absence of one.
 *
 * It does not summarise a refusal. Every desk decision shows its whole ladder,
 * rung by rung, so "why is this follower flat while the leader is long three"
 * is answered from the record.
 *
 * And it does not invent an account state. An account with no rule set linked
 * says so, and the desk refuses to trade it rather than assuming a balance.
 */

type SectionKey = 'desk' | 'allocation' | 'copy' | 'limits' | 'news' | 'desk_activity'

const TITLE: Record<SectionKey, string> = {
  desk: 'Prop Desk',
  allocation: 'Allocation',
  copy: 'Copy Trader',
  limits: 'Desk Limits',
  news: 'News',
  desk_activity: 'Desk Activity',
}

export function PropDeskView({ section }: { section: SectionKey }) {
  return (
    <div className="desk-view af-panel-in">
      <header className="prop-head">
        <div>
          <p className="eyebrow">Prop Desk</p>
          <h1>{TITLE[section]}</h1>
        </div>
      </header>
      {section === 'desk' && <AccountsSection />}
      {section === 'allocation' && <AllocationSection />}
      {section === 'copy' && <CopySection />}
      {section === 'limits' && <LimitsSection />}
      {section === 'news' && <NewsSection />}
      {section === 'desk_activity' && <ActivitySection />}
    </div>
  )
}

/* ── Accounts, connections and what this build can actually reach ─────────── */

function AccountsSection() {
  const providers = useProviders()
  const connections = useConnections()
  const mutations = useDeskMutations()
  const [label, setLabel] = useState('Simulator')

  if (providers.isPending || connections.isPending) {
    return <div className="state" role="status">Reading the desk…</div>
  }

  const catalogue = providers.data
  const rows = connections.data?.connections ?? []
  const accounts = connections.data?.accounts ?? []

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Providers" meta="What this build can reach, and what it cannot" />
        <p className="desk-note">
          A provider is execution and account infrastructure. A platform is a front end:
          it draws charts and sends order entry to whichever provider holds the account.
          They are not interchangeable, and this list says which is which.
        </p>
        <div className="desk-grid">
          {catalogue?.providers.map((provider) => (
            <article key={provider.provider} className="desk-card">
              <header>
                <h3>{provider.display_name}</h3>
                <StatusPill
                  tone={provider.live_connector_implemented ? 'good' : 'unknown'}
                  label={
                    provider.live_connector_implemented
                      ? 'Connector implemented'
                      : 'No live connector'
                  }
                />
              </header>
              <p>{provider.what_it_is}</p>
              <dl className="desk-facts">
                <div><dt>Authentication</dt><dd>{provider.auth_method.replace('_', ' ')}</dd></div>
                <div><dt>Account key</dt><dd>{provider.account_key_fields.join(' + ')}</dd></div>
                <div>
                  <dt>Rate limit</dt>
                  <dd>
                    {provider.rate_limit_per_minute
                      ? `${provider.rate_limit_per_minute}/min per credential`
                      : 'none published'}
                  </dd>
                </div>
              </dl>
              {provider.limitations.length > 0 && (
                <Limitations items={provider.limitations} />
              )}
              {!provider.live_connector_implemented &&
                catalogue?.required_work[provider.provider] && (
                  <details className="desk-details">
                    <summary>What a live connector would need</summary>
                    <RequiredWorkList work={catalogue.required_work[provider.provider]} />
                  </details>
                )}
            </article>
          ))}
        </div>
      </section>

      <section className="desk-block">
        <PanelHead title="Platforms" meta="Front ends, and the provider each one binds to" />
        <table className="desk-table">
          <thead>
            <tr><th>Platform</th><th>Accounts live in</th><th>Note</th></tr>
          </thead>
          <tbody>
            {catalogue?.platforms.map((platform) => (
              <tr key={platform.platform}>
                <td>{platform.display_name}</td>
                <td>
                  {platform.provider ? (
                    platform.provider
                  ) : (
                    <NotMeasured what="Not established" why={platform.signal_source_only
                      ? 'a signal source, not an account system'
                      : 'whichever upstream provider it is connected to'} />
                  )}
                </td>
                <td className="desk-muted">{platform.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="desk-block">
        <PanelHead title="Connections" meta="One credential, many accounts" />
        {rows.length === 0 ? (
          <Empty
            title="No connections yet."
            detail="Only the simulator executes in this build. Add one to exercise the desk end to end."
            action={
              <form
                className="desk-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  mutations.createConnection.mutate({ provider: 'simulated', label })
                }}
              >
                <label>
                  <span>Connection name</span>
                  <input value={label} onChange={(e) => setLabel(e.target.value)} />
                </label>
                <button type="submit">Add simulated connection</button>
              </form>
            }
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr>
                <th>Connection</th><th>Provider</th><th>State</th>
                <th>Heartbeat</th><th>Budget</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((connection) => (
                <tr key={connection.connection_id}>
                  <td>{connection.label}</td>
                  <td>{connection.provider}</td>
                  <td>
                    <StatusPill
                      tone={connection.state === 'live' ? 'good' : 'warn'}
                      label={connection.state}
                    />
                    {connection.last_error && (
                      <p className="desk-muted">{connection.last_error}</p>
                    )}
                  </td>
                  <td>
                    {connection.health?.heartbeat_age_seconds == null ? (
                      <NotMeasured what="Not established" why="never connected" />
                    ) : (
                      `${connection.health.heartbeat_age_seconds.toFixed(0)}s ago`
                    )}
                  </td>
                  <td>
                    {connection.health?.rate_budget_remaining == null ? (
                      <NotMeasured what="Not established" why="not connected" />
                    ) : (
                      `${(connection.health.rate_budget_remaining * 100).toFixed(0)}%`
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() =>
                        connection.state === 'live'
                          ? mutations.disconnect.mutate(connection.connection_id)
                          : mutations.connect.mutate(connection.connection_id)
                      }
                    >
                      {connection.state === 'live' ? 'Disconnect' : 'Connect'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Accounts" meta="Provider-qualified, so a rename changes nothing" />
        {accounts.length === 0 ? (
          <Empty
            title="No accounts."
            detail="A real provider's accounts are discovered from the provider. A simulated connection can be given one."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr>
                <th>Account</th><th>Identity</th><th>Balance</th><th>Equity</th>
                <th>Rules</th><th>Policy</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.account_uid}>
                  <td>{account.display_name || account.account_uid}</td>
                  <td className="desk-mono">{account.canonical}</td>
                  <td>
                    {account.balance == null
                      ? <NotMeasured what="Not established" why="the provider has not reported one" />
                      : account.balance.toLocaleString()}
                  </td>
                  <td>
                    {account.equity == null
                      ? <NotMeasured what="Not established" why="the provider has not reported one" />
                      : account.equity.toLocaleString()}
                  </td>
                  <td>
                    {account.prop_account_id
                      ? 'linked'
                      : <NotMeasured what="Not established" why="no rule set is linked, so this account cannot be assessed or traded" />}
                  </td>
                  <td>
                    {account.policy_id
                      ? 'recorded'
                      : <NotMeasured what="Not established" why="no firm permissions recorded; unknown is not permission" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

function RequiredWorkList({ work }: { work: { engineering: string[]; external: string[]; credentials: string[]; open_questions: string[] } }) {
  const groups: [string, string[]][] = [
    ['Engineering', work.engineering],
    ['External — not buyable with engineering time', work.external],
    ['Credentials it would need', work.credentials],
    ['Still open', work.open_questions],
  ]
  return (
    <>
      {groups.map(([heading, items]) =>
        items.length === 0 ? null : (
          <div key={heading} className="desk-worklist">
            <h4>{heading}</h4>
            <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        ),
      )}
    </>
  )
}

/* ── Firm permissions ─────────────────────────────────────────────────────── */

function LimitsSection() {
  const policies = usePolicies()
  const connections = useConnections()
  if (policies.isPending) return <div className="state" role="status">Reading policies…</div>

  const items = policies.data?.policies ?? []
  const questions = policies.data?.questions ?? []
  const accounts = connections.data?.accounts ?? []

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Firm permissions" meta="Recorded by you, never assumed" />
        <p className="desk-note">{policies.data?.note}</p>
        {items.length === 0 ? (
          <Empty
            title="No programme policy has been recorded."
            detail="Until one is, every permission reads UNKNOWN and the desk refuses every automatic action on the account. That is the honest starting position."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr>
                <th>Programme</th>
                {questions.map((q) => <th key={q.key} title={q.question}>{q.key.replace(/_/g, ' ')}</th>)}
              </tr>
            </thead>
            <tbody>
              {items.map((policy) => (
                <tr key={policy.policy_id}>
                  <td>
                    {policy.firm_label || 'unlabelled'}
                    {policy.program_label && ` · ${policy.program_label}`}
                  </td>
                  {questions.map((q) => {
                    const value = policy[q.key as keyof typeof policy] as Permission
                    return (
                      <td key={q.key}>
                        <StatusPill
                          tone={PERMISSION_TONE[value] ?? 'unknown'}
                          label={PERMISSION_LABEL[value] ?? String(value)}
                        />
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Contract caps" meta="The smallest known limit is the binding one" />
        {accounts.length === 0 ? (
          <Empty
            title="No accounts on any connection."
            detail="Discover them from a provider, or add one to a simulated connection."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr><th>Account</th><th>Provider cap</th><th>Can trade</th><th>Order types</th></tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.account_uid}>
                  <td>{account.display_name || account.account_uid}</td>
                  <td>
                    {account.capability.max_contracts == null
                      ? <NotMeasured what="Not established" why="not reported by the provider" />
                      : account.capability.max_contracts}
                  </td>
                  <td>
                    {account.capability.can_trade == null
                      ? <NotMeasured what="Not established" why="not established" />
                      : account.capability.can_trade ? 'yes' : 'no'}
                  </td>
                  <td>
                    {account.capability.order_types.length === 0
                      ? <NotMeasured what="Not established" why="capability not discovered" />
                      : account.capability.order_types.join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

/* ── Copy trader ──────────────────────────────────────────────────────────── */

function CopySection() {
  const groups = useGroups()
  const mutations = useDeskMutations()
  const [symbol, setSymbol] = useState('MNQ')
  const [position, setPosition] = useState(1)
  const [result, setResult] = useState<{
    copy: CopyDecision[]
    desk: DeskDecision[]
    dryRun: boolean
  } | null>(null)

  if (groups.isPending) return <div className="state" role="status">Reading groups…</div>
  const items = groups.data?.groups ?? []

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Copy groups" meta="One leader, many followers, one owner" />
        {items.length === 0 ? (
          <Empty
            title="No copy groups."
            detail="A group needs an attestation that every account in it belongs to one owner. Copying between different people's accounts is prohibited at every firm the research examined that addressed it."
          />
        ) : (
          items.map((group) => (
            <article key={group.group_id} className="desk-card">
              <header>
                <h3>{group.name}</h3>
                <StatusPill
                  tone={group.active ? 'good' : 'unknown'}
                  label={group.active ? 'Replicating' : 'Stopped'}
                />
              </header>
              <p className="desk-muted">
                Leader {group.leader.account_uid} · attested by {group.owner_attested_by}
              </p>
              <table className="desk-table">
                <thead>
                  <tr><th>Follower</th><th>Mode</th><th>Sizing</th><th>Mapping</th><th>Caps</th></tr>
                </thead>
                <tbody>
                  {group.followers.map((follower) => (
                    <tr key={follower.account_uid}>
                      <td>{follower.account_uid}</td>
                      <td>{follower.mode.replace(/_/g, ' ')}</td>
                      <td>
                        {follower.sizing.method} × {follower.sizing.value}
                        {` (${follower.sizing.rounding})`}
                      </td>
                      <td>
                        {follower.sizing.mapping}
                        {follower.sizing.target_root && ` → ${follower.sizing.target_root}`}
                      </td>
                      <td>
                        {follower.sizing.max_position_contracts ?? '—'} position ·{' '}
                        {follower.sizing.max_contracts_per_order ?? '—'} per order
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="desk-form">
                <label>
                  <span>Product</span>
                  <input value={symbol} onChange={(e) => setSymbol(e.target.value)} />
                </label>
                <label>
                  <span>Leader net position</span>
                  <input
                    type="number"
                    value={position}
                    onChange={(e) => setPosition(Number(e.target.value))}
                  />
                </label>
                <button
                  type="button"
                  onClick={() =>
                    mutations.copyPass
                      .mutateAsync({
                        groupId: group.group_id,
                        symbol,
                        leader_position: position,
                        dry_run: true,
                      })
                      .then((data) =>
                        setResult({
                          copy: data.copy_decisions,
                          desk: data.desk_decisions,
                          dryRun: data.dry_run,
                        }),
                      )
                  }
                >
                  What would this do?
                </button>
                <button
                  type="button"
                  onClick={() =>
                    mutations.setGroupActive.mutate({
                      groupId: group.group_id,
                      active: !group.active,
                    })
                  }
                >
                  {group.active ? 'Stop replicating' : 'Start replicating'}
                </button>
              </div>
            </article>
          ))
        )}
      </section>

      {result && (
        <section className="desk-block">
          <PanelHead
            title={result.dryRun ? 'What each follower would do' : 'What each follower did'}
            meta="Sizing, then the ladder"
          />
          <table className="desk-table">
            <thead>
              <tr><th>Follower</th><th>Outcome</th><th>Target</th><th>Why</th></tr>
            </thead>
            <tbody>
              {result.copy.map((decision) => (
                <tr key={decision.follower_account_uid}>
                  <td>{decision.follower_account_uid}</td>
                  <td>
                    <StatusPill
                      tone={decision.outcome === 'intent' ? 'good' : 'warn'}
                      label={decision.outcome.replace(/_/g, ' ')}
                    />
                  </td>
                  <td>
                    {decision.follower_position} → {decision.target_position}
                    {decision.mapping?.exposure_error != null &&
                      decision.mapping.exposure_error !== 0 && (
                        <span className="desk-muted">
                          {' '}({(decision.mapping.exposure_error * 100).toFixed(0)}% exposure error)
                        </span>
                      )}
                  </td>
                  <td className="desk-muted">{decision.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <DecisionLadder decisions={result.desk} />
        </section>
      )}
    </>
  )
}

/** Every rung, for every decision. The refusals are the useful half. */
function DecisionLadder({ decisions }: { decisions: DeskDecision[] }) {
  if (decisions.length === 0) return null
  return (
    <div className="desk-ladder">
      {decisions.map((decision) => (
        <details key={decision.decision_id} open={!decision.cleared}>
          <summary>
            <StatusPill
              tone={decision.cleared ? 'good' : 'bad'}
              label={decision.cleared ? 'Cleared' : 'Refused'}
            />
            <span>
              {decision.intent.side} {decision.intent.quantity} {decision.intent.symbol} on{' '}
              {decision.intent.account_uid}
            </span>
          </summary>
          <ol className="desk-stages">
            {decision.stages.map((stage) => (
              <li key={stage.stage} data-passed={stage.passed}>
                <StatusPill
                  tone={stage.passed ? 'good' : stage.unknown ? 'unknown' : 'bad'}
                  label={stage.stage.replace(/_/g, ' ')}
                />
                <span>{stage.detail}</span>
              </li>
            ))}
          </ol>
          {decision.acknowledgement && !decision.acknowledgement.accepted && (
            <p className="desk-muted">
              The provider refused it: {decision.acknowledgement.reason}
            </p>
          )}
        </details>
      ))}
    </div>
  )
}

/* ── Allocation ───────────────────────────────────────────────────────────── */

function AllocationSection() {
  const allocation = useAllocation()
  const mutations = useDeskMutations()
  const [strategies, setStrategies] = useState('')
  const [plan, setPlan] = useState<{
    allocations: AllocationRow[]
    candidates: { account_uid: string; strategy_id: string; feasible: boolean; detail: string; max_contracts: number; health_grade: string }[]
    advice_rejected: string[]
    advice_clamped: string[]
    limitations: string[]
  } | null>(null)

  if (allocation.isPending) return <div className="state" role="status">Reading allocations…</div>
  const current = allocation.data?.allocations ?? []
  const history = allocation.data?.history ?? []

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Current allocation" meta="Which account runs which strategy" />
        {current.length === 0 ? (
          <Empty
            title="No account is running an allocated strategy."
            detail="An allocation needs a strategy the judge passed, an out-of-sample drawdown estimate to size against, a recorded firm permission, and an account whose rules can be evaluated."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr><th>Account</th><th>Strategy</th><th>Contracts</th><th>Source</th><th>Why</th></tr>
            </thead>
            <tbody>
              {current.map((row) => (
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
                  <td className="desk-muted">{row.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Recommend" meta="Deterministic feasibility first; advice can only narrow" />
        <div className="desk-form">
          <label>
            <span>Strategy ids, comma separated</span>
            <input
              value={strategies}
              onChange={(e) => setStrategies(e.target.value)}
              placeholder="mnq_session_momentum, es_mean_revert"
            />
          </label>
          <button
            type="button"
            onClick={() =>
              mutations.planAllocation
                .mutateAsync({
                  strategy_ids: strategies
                    .split(',')
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
                .then((data) => setPlan(data.plan))
            }
          >
            Which strategy should each account run?
          </button>
        </div>

        {plan && (
          <>
            <h3>Every pairing considered</h3>
            <table className="desk-table">
              <thead>
                <tr><th>Account</th><th>Strategy</th><th>Feasible</th><th>Size</th><th>Health</th><th>Reason</th></tr>
              </thead>
              <tbody>
                {plan.candidates.map((candidate) => (
                  <tr key={`${candidate.account_uid}:${candidate.strategy_id}`}>
                    <td>{candidate.account_uid}</td>
                    <td>{candidate.strategy_id}</td>
                    <td>
                      <StatusPill
                        tone={candidate.feasible ? 'good' : 'bad'}
                        label={candidate.feasible ? 'yes' : 'no'}
                      />
                    </td>
                    <td>{candidate.feasible ? candidate.max_contracts : '—'}</td>
                    <td>{candidate.health_grade}</td>
                    <td className="desk-muted">{candidate.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {plan.advice_rejected.length > 0 && (
              <Limitations items={plan.advice_rejected} />
            )}
            {plan.advice_clamped.length > 0 && <Limitations items={plan.advice_clamped} />}
            <Limitations items={plan.limitations} />
            {plan.allocations.length > 0 && (
              <button
                type="button"
                onClick={() =>
                  mutations.applyAllocation.mutate({
                    allocations: plan.allocations,
                    actor: 'operator',
                  })
                }
              >
                Apply {plan.allocations.length} allocation(s)
              </button>
            )}
          </>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="History" meta="Append-only: what changed, when and why" />
        {history.length === 0 ? (
          <Empty
            title="No allocation has changed yet."
            detail="Every start, stop, switch and resize is recorded here as it happens."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr><th>When</th><th>Account</th><th>Change</th><th>Strategy</th><th>Why</th></tr>
            </thead>
            <tbody>
              {history.map((change, index) => (
                <tr key={`${change.account_uid}-${change.at}-${index}`}>
                  <td>{new Date(change.at).toLocaleString()}</td>
                  <td>{change.account_uid}</td>
                  <td>{change.kind}</td>
                  <td>
                    {change.previous_strategy_id && change.previous_strategy_id !== change.strategy_id
                      ? `${change.previous_strategy_id} → ${change.strategy_id || 'none'}`
                      : change.strategy_id || 'none'}
                  </td>
                  <td className="desk-muted">{change.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

/* ── News ─────────────────────────────────────────────────────────────────── */

function NewsSection() {
  const news = useNews(14)
  if (news.isPending) return <div className="state" role="status">Reading the calendar…</div>
  const state = news.data
  if (!state) return <Empty
      title="The calendar could not be read."
      detail="No source answered. Record events by hand, or set FRED_API_KEY."
    />

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Blackout" meta="News can add a restriction; it can never remove one" />
        <div className="desk-stats">
          <Stat
            label="Window"
            value={state.assessment.restricted ? 'Open' : 'Closed'}
            note={state.assessment.restricted ? state.assessment.action : 'nothing is blocked'}
          />
          <Stat
            label="Next event"
            value={state.assessment.next_event?.title ?? '—'}
            note={
              state.assessment.minutes_to_next == null
                ? 'none scheduled in range'
                : `in ${state.assessment.minutes_to_next.toFixed(0)} minutes`
            }
          />
          <Stat
            label="Policy"
            value={state.policy.enabled ? 'Enabled' : 'Off'}
            note={`${state.policy.minutes_before}m before, ${state.policy.minutes_after}m after`}
          />
        </div>
        {state.assessment.gaps.length > 0 && <Limitations items={state.assessment.gaps} />}
      </section>

      <section className="desk-block">
        <PanelHead title="Sources" meta="What was investigated, and what is wired" />
        <table className="desk-table">
          <thead><tr><th>Source</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>
            {state.sources.map((source) => (
              <tr key={source.name}>
                <td>
                  <a href={source.url} target="_blank" rel="noreferrer noopener">{source.name}</a>
                </td>
                <td>
                  <StatusPill
                    tone={
                      source.status === 'implemented'
                        ? 'good'
                        : source.status === 'unsuitable'
                          ? 'bad'
                          : 'unknown'
                    }
                    label={source.status}
                  />
                </td>
                <td className="desk-muted">{source.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {state.availability.map((item) => (
          <p key={item.provider} className="desk-muted">
            <strong>{item.provider}</strong>: {item.reason}
            {item.attribution && <> — {item.attribution}</>}
          </p>
        ))}
      </section>

      <section className="desk-block">
        <PanelHead title="Scheduled events" meta="The next fortnight" />
        {state.events.length === 0 ? (
          <Empty
            title="No events in range."
            detail="Record them by hand, or set FRED_API_KEY to read the Federal Reserve Bank of St. Louis release calendar."
          />
        ) : (
          <table className="desk-table">
            <thead><tr><th>When</th><th>Event</th><th>Impact</th><th>Source</th></tr></thead>
            <tbody>
              {state.events.map((event) => (
                <tr key={event.event_id}>
                  <td>
                    {new Date(event.at).toLocaleString()}
                    {event.time_is_approximate && (
                      <span className="desk-muted"> (date only)</span>
                    )}
                  </td>
                  <td>{event.title}</td>
                  <td>
                    <StatusPill
                      tone={
                        event.impact === 'high'
                          ? 'bad'
                          : event.impact === 'medium'
                            ? 'warn'
                            : event.impact === 'low'
                              ? 'warn'
                              : 'unknown'
                      }
                      label={event.impact}
                    />
                  </td>
                  <td className="desk-muted">{event.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

/* ── Activity ─────────────────────────────────────────────────────────────── */

function ActivitySection() {
  const activity = useDeskActivity(200)
  if (activity.isPending) return <div className="state" role="status">Reading the record…</div>
  const state = activity.data
  if (!state) return <Empty
      title="Nothing recorded."
      detail="The desk writes a row for every order, refusal and reconciliation pass. None has run."
    />

  return (
    <>
      <section className="desk-block">
        <PanelHead title="Connection health" meta="Why copying is or is not working" />
        {state.health.length === 0 ? (
          <Empty
            title="No connections."
            detail="Add one on the Prop Desk screen; the desk has nothing to report until then."
          />
        ) : (
          <table className="desk-table">
            <thead>
              <tr><th>Connection</th><th>State</th><th>Failures</th><th>Queued</th><th>Last error</th></tr>
            </thead>
            <tbody>
              {state.health.map((health) => (
                <tr key={health.connection_id}>
                  <td>{health.connection_id}</td>
                  <td>
                    <StatusPill
                      tone={health.state === 'live' ? 'good' : 'warn'}
                      label={health.state}
                    />
                  </td>
                  <td>{health.consecutive_failures}</td>
                  <td>{health.queued_commands}</td>
                  <td className="desk-muted">{health.last_error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Orders and refusals" meta="Every rung, for every decision" />
        {state.decisions.length === 0 ? (
          <Empty
            title="Nothing has been screened yet."
            detail="Run a copy pass, or let an allocated strategy propose an order."
          />
        ) : (
          <DecisionLadder decisions={state.decisions} />
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Reconciliation" meta="The provider is authoritative" />
        {state.reconciliations.length === 0 ? (
          <Empty
            title="No reconciliation pass has run."
            detail="Reconcile an account to compare it against its provider's own state."
          />
        ) : (
          state.reconciliations.map((report, index) => (
            <article key={`${report.account_uid}-${report.at}-${index}`} className="desk-card">
              <header>
                <h3>{report.account_uid}</h3>
                <StatusPill
                  tone={report.state === 'in_sync' ? 'good' : 'warn'}
                  label={report.state.replace(/_/g, ' ')}
                />
              </header>
              <p className="desk-muted">
                {report.trigger} · {new Date(report.at).toLocaleString()}
              </p>
              {report.divergences.length > 0 && (
                <ul className="desk-worklist">
                  {report.divergences.map((divergence, i) => (
                    <li key={`${divergence.kind}-${i}`}>
                      <strong>{divergence.kind.replace(/_/g, ' ')}</strong>
                      {divergence.symbol && ` (${divergence.symbol})`}: {divergence.detail}
                    </li>
                  ))}
                </ul>
              )}
              {report.limitations.length > 0 && <Limitations items={report.limitations} />}
            </article>
          ))
        )}
      </section>
    </>
  )
}
