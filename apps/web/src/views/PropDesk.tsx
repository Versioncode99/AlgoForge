import { useState } from 'react'
import { Empty, PanelHead, Stat } from '../components/ui'
import { Limitations, NotMeasured, StatusPill } from '../components/measures'
import {
  PERMISSION_LABEL,
  PERMISSION_TONE,
  type AllocationRow,
  type AutonomyLevel,
  type CopyDecision,
  type DeskDecision,
  type Disclosure,
  type Permission,
  type RiskBand,
  type RiskDriver,
  type RiskMode,
  useAllocation,
  useConnections,
  useDeskActivity,
  useDeskAudit,
  useDeskMutations,
  useGroups,
  useNews,
  usePolicies,
  useProviders,
  useRisk,
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
 *
 * The Risk and AI Management screens add two of their own.
 *
 * The appetite meter is never drawn as a size dial. Under the number sits what
 * that appetite actually sizes against — a bootstrapped drawdown quantile and
 * the contracts it buys on this account — because the meter's effect is a
 * property of the strategy's measured tail rather than of the meter.
 *
 * A consequential setting is never switched on without its disclosure. The
 * dialog appears where the switch is, every statement has its own checkbox, and
 * the confirm button stays disabled until all of them are ticked.
 */

type SectionKey =
  | 'desk'
  | 'allocation'
  | 'copy'
  | 'limits'
  | 'news'
  | 'desk_activity'
  | 'risk_management'
  | 'ai_management'

const TITLE: Record<SectionKey, string> = {
  desk: 'Prop Desk',
  allocation: 'Allocation',
  copy: 'Copy Trader',
  limits: 'Desk Limits',
  news: 'News',
  desk_activity: 'Desk Activity',
  risk_management: 'Risk Management',
  ai_management: 'AI Management',
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
      {section === 'risk_management' && <RiskSection />}
      {section === 'ai_management' && <AiSection />}
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

/* ── Risk: who decides how large a position is ─────────────────────────────── */

/** The safety disclosure, shown where the thing is being switched on.
 *
 * Every statement has its own checkbox and the confirm button stays disabled
 * until all of them are ticked — because a disclosure with two acknowledgements
 * is making two separate statements, and "they ticked one" is a different fact
 * from "they ticked both".
 */
function DisclosureDialog({
  disclosure,
  onCancel,
  onConfirm,
  busy,
}: {
  disclosure: Disclosure
  onCancel: () => void
  onConfirm: (accepted: string[]) => void
  busy?: boolean
}) {
  const [ticked, setTicked] = useState<string[]>([])
  const complete = disclosure.acknowledgements.every((item) => ticked.includes(item))
  return (
    <div className="desk-disclosure" role="dialog" aria-label={disclosure.title}>
      <header>
        <StatusPill tone="warn" label="Before this takes effect" />
        <h3>{disclosure.title}</h3>
      </header>
      {disclosure.body.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
      <ul className="desk-ticks">
        {disclosure.acknowledgements.map((statement) => (
          <li key={statement}>
            <label>
              <input
                type="checkbox"
                checked={ticked.includes(statement)}
                onChange={(event) =>
                  setTicked((current) =>
                    event.target.checked
                      ? [...current, statement]
                      : current.filter((item) => item !== statement),
                  )
                }
              />
              <span>{statement}</span>
            </label>
          </li>
        ))}
      </ul>
      <footer>
        <button type="button" onClick={onCancel}>
          {disclosure.cancel_label}
        </button>
        <button
          type="button"
          data-primary="yes"
          disabled={!complete || busy}
          onClick={() => onConfirm(disclosure.acknowledgements)}
        >
          {disclosure.confirm_label}
        </button>
      </footer>
      <p className="desk-muted">Version {disclosure.version}. Recorded against your name.</p>
    </div>
  )
}

/** Conservative to Aggressive, drawn against what it actually buys.
 *
 * The number under the meter is the appetite; the line under that is what the
 * appetite costs on this strategy, which is the part that differs between a
 * thin-tailed strategy and a fat-tailed one. Showing only the appetite would
 * make the meter look like a size dial, which is precisely what it is not.
 */
function AppetiteMeter({
  appetite,
  band,
  onChange,
  disabled,
}: {
  appetite: number
  band: RiskBand | null
  onChange?: (value: number) => void
  disabled?: boolean
}) {
  return (
    <div className="desk-meter">
      <div className="desk-meter-track">
        <span>Conservative</span>
        <input
          type="range"
          min={0}
          max={100}
          value={appetite}
          disabled={disabled || !onChange}
          onChange={(event) => onChange?.(Number(event.target.value))}
          aria-label="Risk appetite"
        />
        <span>Aggressive</span>
      </div>
      <div className="desk-meter-read">
        <b className="desk-meter-value">{appetite}</b>
        <span>{appetite <= 33 ? 'Conservative' : appetite >= 67 ? 'Aggressive' : 'Balanced'}</span>
      </div>
      {band ? (
        <p className="desk-muted">
          Sizes against the {(band.quantile * 100).toFixed(0)}th-percentile bootstrapped
          drawdown of {band.drawdown_per_contract.toLocaleString(undefined, {
            maximumFractionDigits: 0,
          })}{' '}
          per contract, at {(band.target_fraction * 100).toFixed(2)}% of this account&rsquo;s
          buffer &mdash; {band.contracts} contract{band.contracts === 1 ? '' : 's'}.
        </p>
      ) : (
        <p className="desk-muted">
          No risk band has been derived for this account yet, so the meter has nothing to
          size against.
        </p>
      )}
    </div>
  )
}

/** Every driver, measured or not, with what it did to the target. */
function DriverTable({ drivers }: { drivers: RiskDriver[] }) {
  if (drivers.length === 0) return null
  return (
    <table className="desk-table">
      <thead>
        <tr>
          <th>Driver</th>
          <th>Observed</th>
          <th>Effect</th>
          <th>Why</th>
        </tr>
      </thead>
      <tbody>
        {drivers.map((driver) => (
          <tr key={driver.kind}>
            <td>{driver.label}</td>
            <td>
              {driver.measured ? (
                driver.observed
              ) : (
                <StatusPill tone="unknown" label="Not measured" />
              )}
            </td>
            <td className="desk-mono">
              {driver.measured ? `x${driver.effect.toFixed(2)}` : '—'}
            </td>
            <td>{driver.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function RiskSection() {
  const risk = useRisk()
  const connections = useConnections()
  const mutations = useDeskMutations()
  const [selected, setSelected] = useState('')
  const [pending, setPending] = useState<{ mode: RiskMode; appetite: number } | null>(null)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')

  if (risk.isLoading) return <p className="desk-muted">Reading risk configuration…</p>
  if (risk.error) return <Empty title="Risk is unavailable" detail={String(risk.error)} />

  const rows = risk.data?.accounts ?? []
  const catalogue = risk.data?.catalogue
  const accounts = connections.data?.accounts ?? []
  const account = selected || rows[0]?.account_uid || ''
  const row = rows.find((item) => item.account_uid === account)
  const name =
    accounts.find((item) => item.account_uid === account)?.display_name || account

  if (rows.length === 0) {
    return (
      <Empty
        title="No accounts to configure"
        detail="Connect an account on the Prop Desk screen first. Risk is configured per account, because the buffer it is a fraction of belongs to one account."
      />
    )
  }

  const aiDisclosure = catalogue?.disclosures.find((item) => item.key === 'ai_risk_management')

  const save = (mode: RiskMode, appetite: number) => {
    setError('')
    const body: Record<string, unknown> = { account_uid: account, mode, appetite }
    if (mode === 'manual') {
      body.manual = {
        risk_fraction: row?.state?.current_fraction ?? 0.1,
        max_contracts: row?.settings?.boundaries.max_contracts ?? 2,
      }
    }
    if (mode === 'ai_managed') body.ai_capabilities = ['position_sizing', 'risk_reduction']
    mutations.saveRiskSettings.mutate(body, {
      onError: (cause) => setError(String(cause)),
      onSuccess: () => setPending(null),
    })
  }

  return (
    <>
      <section className="desk-block">
        <PanelHead
          title="Risk management"
          meta={`${rows.length} account${rows.length === 1 ? '' : 's'}`}
        />
        <label className="desk-select">
          <span>Account</span>
          <select value={account} onChange={(event) => setSelected(event.target.value)}>
            {rows.map((item) => (
              <option key={item.account_uid} value={item.account_uid}>
                {accounts.find((a) => a.account_uid === item.account_uid)?.display_name ||
                  item.account_uid}
              </option>
            ))}
          </select>
        </label>

        <div className="desk-modes">
          {catalogue?.modes.map((mode) => {
            const active = row?.settings?.mode === mode.mode
            return (
              <button
                key={mode.mode}
                type="button"
                className="desk-mode"
                data-active={active ? 'yes' : undefined}
                onClick={() => {
                  if (mode.mode === 'ai_managed' && aiDisclosure) {
                    setPending({ mode: mode.mode, appetite: row?.settings?.appetite ?? 50 })
                    return
                  }
                  save(mode.mode, row?.settings?.appetite ?? 50)
                }}
              >
                <span className="desk-mode-dot" aria-hidden="true" />
                <b>{mode.promise}</b>
                <span className="desk-mode-name">{mode.mode.replace(/_/g, ' ')}</span>
                <p>{mode.detail}</p>
              </button>
            )
          })}
        </div>
        {error && <p className="desk-error">{error}</p>}
      </section>

      {pending && aiDisclosure && (
        <DisclosureDialog
          disclosure={aiDisclosure}
          busy={mutations.acknowledge.isPending}
          onCancel={() => setPending(null)}
          onConfirm={(accepted) =>
            mutations.acknowledge.mutate(
              { key: aiDisclosure.key, accepted },
              {
                onError: (cause) => setError(String(cause)),
                onSuccess: () => save(pending.mode, pending.appetite),
              },
            )
          }
        />
      )}

      {row?.settings ? (
        <>
          <section className="desk-block">
            <PanelHead
              title="Risk profile"
              meta={row.settings.mode.replace(/_/g, ' ')}
            />
            <AppetiteMeter
              appetite={row.settings.appetite}
              band={row.last_proposal?.band ?? null}
              disabled={row.settings.mode === 'manual'}
              onChange={
                row.settings.mode === 'manual'
                  ? undefined
                  : (value) => save(row.settings!.mode, value)
              }
            />
            <div className="desk-stats">
              <Stat
                label="Current risk"
                value={
                  row.state ? `${(row.state.current_fraction * 100).toFixed(2)}%` : 'Not set'
                }
                note="of this account's buffer to its loss floor"
              />
              <Stat
                label="Your ceiling"
                value={`${(row.settings.boundaries.maximum_fraction * 100).toFixed(2)}%`}
                note="nothing proposes above this"
              />
              <Stat
                label="Position size"
                value={
                  row.last_proposal?.contracts_after === null ||
                  row.last_proposal?.contracts_after === undefined
                    ? 'Not measured'
                    : `${row.last_proposal.contracts_after} contracts`
                }
                tone={row.last_proposal?.contracts_after ? 'plain' : 'unknown'}
              />
              <Stat
                label="Autonomous deployment"
                value={row.autonomy.replace(/_/g, ' ')}
                tone={row.autonomy === 'off' ? 'plain' : 'warn'}
              />
            </div>
            {/* An account that is not running a strategy has no measured
              * drawdown to size against, so the band is unmeasurable and risk
              * sits at the minimum. Naming one asks "what would this cost on
              * that strategy" — a preview, not a change to what the account is
              * actually doing. */}
            <div className="desk-form">
              {!row.last_proposal?.strategy_id && (
                <label>
                  <span>Size against</span>
                  <input
                    value={preview}
                    placeholder="strategy id"
                    onChange={(event) => setPreview(event.target.value)}
                  />
                </label>
              )}
              <button
                type="button"
                className="desk-action"
                disabled={mutations.evaluateRisk.isPending || row.settings.mode === 'manual'}
                onClick={() =>
                  mutations.evaluateRisk.mutate(
                    { accountUid: account, strategy_id: preview || undefined },
                    { onError: (cause) => setError(String(cause)) },
                  )
                }
              >
                Evaluate now
              </button>
            </div>
            {row.settings.mode === 'manual' && (
              <p className="desk-muted">
                This account is on manual risk. Nothing evaluates or adjusts it; your account
                rules, the refusal ladder and the pre-trade gate still apply.
              </p>
            )}
          </section>

          <section className="desk-block">
            <PanelHead
              title="Why?"
              meta={row.last_proposal ? row.last_proposal.at.slice(0, 16).replace('T', ' ') : ''}
            />
            {row.last_proposal ? (
              <>
                {/* Both lists are derived server-side and added when a stored
                  * proposal is rendered. A row written by an older schema comes
                  * back without them, and a blank section is a better answer
                  * than a screen that will not draw. */}
                <ul className="desk-why">
                  {(row.last_proposal.why ?? []).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
                <DriverTable drivers={row.last_proposal.drivers ?? []} />
              </>
            ) : (
              <NotMeasured
                what="Nothing has been evaluated for this account yet"
                why="The drivers, the band and the governor all report against an evaluation. Run one to see what they say."
              />
            )}
          </section>

          <section className="desk-block">
            <PanelHead title="Your boundaries" meta={row.settings.boundaries.boundaries_hash.slice(0, 8)} />
            <p className="desk-note">
              Every one of these constrains an increase strictly and a decrease leniently.
              De-risking is never delayed by a cooldown or a daily limit.
            </p>
            <table className="desk-table">
              <tbody>
                {[
                  ['Minimum risk', `${(row.settings.boundaries.minimum_fraction * 100).toFixed(2)}%`],
                  ['Maximum risk', `${(row.settings.boundaries.maximum_fraction * 100).toFixed(2)}%`],
                  ['Largest single change', `${(row.settings.boundaries.max_step * 100).toFixed(2)}%`],
                  ['Cooldown between increases', `${row.settings.boundaries.cooldown_minutes} minutes`],
                  ['Hysteresis band', `${(row.settings.boundaries.hysteresis * 100).toFixed(2)}%`],
                  ['Daily change limit', `${(row.settings.boundaries.max_daily_change * 100).toFixed(2)}%`],
                  ['Contract ceiling', `${row.settings.boundaries.max_contracts}`],
                  [
                    'Emergency de-risk below',
                    `${(row.settings.boundaries.emergency_buffer_ratio * 100).toFixed(0)}% of the starting buffer`,
                  ],
                ].map(([label, value]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td className="desk-mono">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : (
        <Empty
          title={`${name} has no risk configuration`}
          detail="Choose a risk mode above. Until one is set, nothing can size a position on this account."
        />
      )}
    </>
  )
}

/* ── AI management ────────────────────────────────────────────────────────── */

function AiSection() {
  const risk = useRisk()
  const audit = useDeskAudit()
  const mutations = useDeskMutations()
  const [error, setError] = useState('')
  const [pendingAutonomy, setPendingAutonomy] = useState<{
    accountUid: string
    level: AutonomyLevel
  } | null>(null)

  if (risk.isLoading) return <p className="desk-muted">Reading AI configuration…</p>
  const catalogue = risk.data?.catalogue
  const rows = risk.data?.accounts ?? []
  const managed = rows.filter((row) => row.settings?.mode === 'ai_managed')
  const strategies = new Set(
    rows.map((row) => row.last_proposal?.strategy_id).filter((item): item is string => !!item),
  )
  const deploymentDisclosure = catalogue?.disclosures.find(
    (item) => item.key === 'autonomous_deployment',
  )

  return (
    <>
      <section className="desk-block">
        <PanelHead
          title="AI management"
          meta={
            <StatusPill
              tone={managed.length > 0 ? 'warn' : 'plain'}
              label={managed.length > 0 ? 'Active' : 'Off'}
            />
          }
        />
        <div className="desk-stats">
          <Stat label="Accounts managed" value={managed.length} note={`of ${rows.length}`} />
          <Stat label="Strategies" value={strategies.size} note="with a recorded evaluation" />
          <Stat
            label="Autonomous deployment"
            value={rows.filter((row) => row.autonomy !== 'off').length}
            note="accounts above off"
          />
        </div>
        {managed.length === 0 && (
          <p className="desk-muted">
            No account is on AI risk management. Turn it on from the Risk screen; it asks you
            to read a disclosure first.
          </p>
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="What it may do" />
        <ul className="desk-permissions">
          {catalogue?.capabilities.map((capability) => {
            const granted = managed.some((row) =>
              row.settings?.ai_capabilities.includes(capability.capability),
            )
            return (
              <li key={capability.capability} data-granted={granted ? 'yes' : 'no'}>
                <span aria-hidden="true">{granted ? '✓' : '—'}</span>
                <div>
                  <b>{capability.label}</b>
                  <p>{capability.detail}</p>
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="desk-block">
        <PanelHead title="What it may never do" meta="and what stops it" />
        <p className="desk-note">
          Each line names the deterministic control that refuses it. These are not settings:
          there is no configuration in which any of them becomes permitted.
        </p>
        <ul className="desk-permissions">
          {catalogue?.prohibitions.map((item) => (
            <li key={item.statement} data-granted="never">
              <span aria-hidden="true">✗</span>
              <div>
                <b>{item.statement}</b>
                <p>{item.detail}</p>
                <code className="desk-mono">{item.enforced_by}</code>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="desk-block">
        <PanelHead title="Autonomous deployment" />
        <p className="desk-note">
          The mandatory control list below is identical at every level. The level changes only
          what happens once every control has passed.
        </p>
        <table className="desk-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Level</th>
              <th>Set</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.account_uid}>
                <td className="desk-mono">{row.account_uid}</td>
                <td>
                  <StatusPill
                    tone={row.autonomy === 'off' ? 'plain' : 'warn'}
                    label={row.autonomy.replace(/_/g, ' ')}
                  />
                </td>
                <td>
                  <select
                    value={row.autonomy}
                    onChange={(event) => {
                      const level = event.target.value as AutonomyLevel
                      setError('')
                      if (level !== 'off' && deploymentDisclosure) {
                        setPendingAutonomy({ accountUid: row.account_uid, level })
                        return
                      }
                      mutations.setAutonomy.mutate(
                        { accountUid: row.account_uid, level },
                        { onError: (cause) => setError(String(cause)) },
                      )
                    }}
                  >
                    {catalogue?.autonomy_levels.map((level) => (
                      <option key={level.level} value={level.level}>
                        {level.label}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {error && <p className="desk-error">{error}</p>}
      </section>

      {pendingAutonomy && deploymentDisclosure && (
        <DisclosureDialog
          disclosure={deploymentDisclosure}
          busy={mutations.acknowledge.isPending}
          onCancel={() => setPendingAutonomy(null)}
          onConfirm={(accepted) =>
            mutations.acknowledge.mutate(
              { key: deploymentDisclosure.key, accepted },
              {
                onError: (cause) => setError(String(cause)),
                onSuccess: () =>
                  mutations.setAutonomy.mutate(
                    { accountUid: pendingAutonomy.accountUid, level: pendingAutonomy.level },
                    {
                      onError: (cause) => setError(String(cause)),
                      onSuccess: () => setPendingAutonomy(null),
                    },
                  ),
              },
            )
          }
        />
      )}

      <section className="desk-block">
        <PanelHead title="Mandatory controls" meta={`${catalogue?.mandatory_gates.length ?? 0}`} />
        <table className="desk-table">
          <tbody>
            {catalogue?.mandatory_gates.map((gate) => (
              <tr key={gate.kind}>
                <td>{gate.name}</td>
                <td>{gate.question}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="desk-block">
        <PanelHead title="Deployment evaluations" />
        {audit.data?.deployments.length ? (
          <div className="desk-ladder">
            {audit.data.deployments.slice(0, 10).map((decision) => (
              <details key={`${decision.strategy_id}-${decision.at}`} open={!decision.cleared}>
                <summary>
                  <StatusPill
                    tone={decision.cleared ? 'good' : 'bad'}
                    label={decision.outcome.replace(/_/g, ' ')}
                  />
                  <span>
                    {decision.strategy_id} on {decision.account_uid}
                  </span>
                </summary>
                <ol className="desk-stages">
                  {decision.gates.map((gate) => (
                    <li key={gate.kind} data-passed={gate.passed}>
                      <StatusPill
                        tone={gate.passed ? 'good' : gate.unknown ? 'unknown' : 'bad'}
                        label={gate.name}
                      />
                      <span>{gate.detail || gate.question}</span>
                    </li>
                  ))}
                </ol>
              </details>
            ))}
          </div>
        ) : (
          <NotMeasured
            what="No deployment has been evaluated"
            why="A deployment evaluation runs every mandatory control and records what each one found. Nothing has asked for one yet."
          />
        )}
      </section>

      <section className="desk-block">
        <PanelHead title="Audit trail" meta={`${audit.data?.records.length ?? 0} records`} />
        {audit.data?.records.length ? (
          <table className="desk-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Account</th>
                <th>By</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {audit.data.records.slice(0, 40).map((record) => (
                <tr key={record.record_id}>
                  <td className="desk-mono">{record.at.slice(0, 16).replace('T', ' ')}</td>
                  <td>{record.action.replace(/_/g, ' ')}</td>
                  <td className="desk-mono">{record.account_uid || '—'}</td>
                  <td>{record.actor_name || record.actor}</td>
                  <td>{record.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty
            title="Nothing recorded yet"
            detail="Risk changes, deployment evaluations and acknowledgements are written here as they happen. The table has no update and no delete."
          />
        )}
      </section>
    </>
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
