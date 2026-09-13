import { useState } from 'react'
import type {
  ModelInfo,
  RoutingDecision,
  RoutingMode,
  RoutingRole,
  SettingsPayload,
} from '../types'

/** Model routing, budget enforcement, and external research.
 *
 * Three panels that used to be one table and a row of dollar figures. Each is
 * written against a specific way the old screen was misleading.
 *
 * **The role table covered nine roles and the engine ran ten of its own**, so
 * "a model per role" chose the model for none of the research. Both halves are
 * here now, and the research half is the one a campaign actually spends.
 *
 * **A substitution was invisible.** Every row shows which model *would answer*
 * under the current settings and why — so an assignment the provider cannot
 * serve reads as a substitution rather than as the assignment.
 *
 * **Budget was unconditional.** It is a switch now, and the limits that are not
 * budget are listed beside it so turning it off does not read as turning off
 * the things that keep the process alive.
 */

type Patch = (body: Record<string, unknown>) => void

function ModelSelect({
  label, value, options, onChange, allowNone,
}: {
  label: string
  value: string
  options: ModelInfo[]
  onChange: (value: string) => void
  allowNone?: boolean
}) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {allowNone && <option value="">— none —</option>}
      {options.map((m) => (
        <option key={m.id} value={m.id}>
          {m.label}{m.status === 'needs_credit' ? ' · needs credit' : ''}
        </option>
      ))}
    </select>
  )
}

/** One role's row. The resolved decision is the point of it: an operator
 *  reading "assigned X" while Y answers is the failure this replaces. */
function RoleRow({
  role, settings, decision, patch, advanced,
}: {
  role: RoutingRole
  settings: SettingsPayload
  decision: RoutingDecision | undefined
  patch: Patch
  advanced: boolean
}) {
  const routing = settings.ai.model_routing.roles[role.key] ?? {
    model: '', fallback: '', enabled: true,
  }
  const send = (change: Record<string, unknown>) =>
    patch({ role_routing: { [role.key]: change } })

  return (
    <tr className={routing.enabled ? undefined : 'is-off'}>
      <td>
        <strong>{role.label}</strong>
        <small className="muted"> · {role.demand}</small>
        <div className="muted">{role.detail}</div>
      </td>
      <td>
        <ModelSelect
          label={`Model for ${role.label}`}
          value={routing.model}
          options={settings.models}
          allowNone
          onChange={(model) => send({ model })}
        />
      </td>
      {advanced && (
        <>
          <td>
            <ModelSelect
              label={`Fallback for ${role.label}`}
              value={routing.fallback}
              options={settings.models}
              allowNone
              onChange={(fallback) => send({ fallback })}
            />
          </td>
          <td>
            {role.optional ? (
              <button
                className={routing.enabled ? 'btn tiny primary' : 'btn tiny'}
                onClick={() => send({ enabled: !routing.enabled })}
              >
                {routing.enabled ? 'On' : 'Off'}
              </button>
            ) : (
              <span className="muted" title="Campaigns cannot run without this role.">
                always on
              </span>
            )}
          </td>
        </>
      )}
      <td className={decision?.substituted ? 'warn' : 'muted'}>
        {decision?.model || '—'}
        <div className="muted small">{decision?.reason}</div>
      </td>
    </tr>
  )
}

export function ModelRoutingPanel({
  settings, patch,
}: { settings: SettingsPayload; patch: Patch }) {
  const [advanced, setAdvanced] = useState(false)
  const [showResearch, setShowResearch] = useState(true)
  const routing = settings.ai.model_routing
  const decisions = new Map(settings.routing_preview.map((d) => [d.role, d]))
  const modes: RoutingMode[] = settings.routing_modes
  const substituted = settings.routing_preview.filter((d) => d.substituted)
  const unavailable = settings.routing_preview.filter((d) => !d.model)
  const roles = settings.routing_roles.filter(
    (r) => showResearch || r.kind === 'workflow',
  )

  return (
    <div className="panel">
      <header>
        <h2>Models · what answers, for which job</h2>
        <div className="panel-actions">
          <button
            className={showResearch ? 'btn tiny primary' : 'btn tiny'}
            onClick={() => setShowResearch((v) => !v)}
          >
            {showResearch ? 'Research agents shown' : 'Research agents hidden'}
          </button>
          <button
            className={advanced ? 'btn tiny primary' : 'btn tiny'}
            onClick={() => setAdvanced((v) => !v)}
          >
            {advanced ? 'Fewer columns' : 'Fallback and enable'}
          </button>
        </div>
      </header>
      <div className="panel-body">
        <div className="stack">
          <label className="budget-row">
            <span>
              Routing mode
              <small>
                {modes.find((m) => m.key === routing.mode)?.detail ??
                  'How a choice is made when the assigned model is unavailable.'}
              </small>
            </span>
            <select
              aria-label="Routing mode"
              value={routing.mode}
              onChange={(e) => patch({ routing_mode: e.target.value })}
            >
              {modes.map((m) => (
                <option key={m.key} value={m.key}>{m.label}</option>
              ))}
            </select>
          </label>
          <label className="budget-row">
            <span>Default model<small>Used by any role that names none</small></span>
            <ModelSelect
              label="Default model"
              value={routing.default_model}
              options={settings.models}
              allowNone
              onChange={(v) => patch({ routing_default_model: v })}
            />
          </label>
          <label className="budget-row">
            <span>Global fallback<small>Used when an assignment cannot be served</small></span>
            <ModelSelect
              label="Global fallback model"
              value={routing.fallback_model}
              options={settings.models}
              allowNone
              onChange={(v) => patch({ routing_fallback_model: v })}
            />
          </label>
        </div>

        {substituted.length > 0 && (
          <p className="warning">
            <b>{substituted.length} role(s) are not using the model they are assigned.</b>{' '}
            {substituted[0].reason} Switch routing to <b>Manual</b> to have these calls
            refuse rather than substitute.
          </p>
        )}
        {unavailable.length > 0 && (
          <p className="warning bad">
            {unavailable.length} role(s) have no model available and will not run.
          </p>
        )}

        <table className="tbl role-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Assigned</th>
              {advanced && <th>Fallback</th>}
              {advanced && <th>Enabled</th>}
              <th>Would answer</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <RoleRow
                key={role.key}
                role={role}
                settings={settings}
                decision={decisions.get(role.key)}
                patch={patch}
                advanced={advanced}
              />
            ))}
          </tbody>
        </table>
        <p className="warning">
          Routing exists because the jobs differ in cost profile: hypothesis work is rare
          and hard, tagging is constant and easy. The research agents are the ones a
          campaign spends — they run on their own, many times an hour.
        </p>
      </div>
    </div>
  )
}

/** Budget enforcement, and what it is not.
 *
 * The switch is the feature. An operator running an overnight campaign on a
 * flat-rate subscription is not protected by a dollar ceiling that means
 * nothing on their plan; they are interrupted by it. So it can be off — and
 * the panel says plainly which limits stay on regardless, from the server's
 * own list rather than from a promise written here. */
export function BudgetPanel({
  settings, patch,
}: { settings: SettingsPayload; patch: Patch }) {
  const budget = settings.ai.budget
  const on = budget.enforced
  return (
    <div className="panel">
      <header>
        <h2>Research budget</h2>
        <div className="panel-actions">
          <button
            className={on ? 'btn primary' : 'btn'}
            onClick={() => patch({ budget_enforced: !on })}
          >
            {on ? 'Enforcement ON' : 'Enforcement OFF'}
          </button>
        </div>
      </header>
      <div className="panel-body">
        {!on && (
          <p className="warning">
            <b>No research ceiling is being enforced.</b> Campaigns run until they finish
            or you stop them. Accounting still runs, so what was spent is still measured;
            the system safety limits below are unaffected.
          </p>
        )}
        <div className="stack">
          {([
            ['campaign_experiments', 'Experiments per campaign', 'Zero means no ceiling'],
            ['model_calls_per_day', 'Model calls per day', 'Zero means no ceiling'],
            ['backtests_per_campaign', 'Backtests per campaign', 'Zero means no ceiling'],
            ['wall_clock_minutes', 'Wall clock per campaign, minutes', 'Zero means no ceiling'],
            ['external_research_per_day', 'External retrievals per day', 'Zero means no ceiling'],
            ['daily_usd_hard', 'Daily allocation, USD', 'Planning value; billing is not metered here'],
            ['monthly_usd_hard', 'Monthly allocation, USD', 'Planning value'],
          ] as const).map(([key, label, note]) => (
            <label className="budget-row" key={key}>
              <span>{label}<small>{note}</small></span>
              <input
                type="number" step="1" min="0" disabled={!on}
                defaultValue={budget[key]}
                aria-label={label}
                onBlur={(e) => {
                  const value = Number(e.target.value)
                  if (value !== budget[key]) patch({ budget: { [key]: value } })
                }}
              />
            </label>
          ))}
        </div>
        <h3 className="sub">In force whatever this switch says</h3>
        <table className="tbl">
          <tbody>
            {settings.safety_limits.map((limit) => (
              <tr key={limit.key}>
                <td>{limit.label}</td>
                <td className="mono">{limit.value}</td>
                <td className="muted">{limit.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** External research: on by default, and honest about what it reaches.
 *
 * The retrieval layer has no credential and no arbitrary-URL fetch. It searches
 * two fixed public indexes and records a failure as a failure, so there is no
 * path here that invents a paper. That is why it can default to on. */
export function ResearchPanel({
  settings, patch,
}: { settings: SettingsPayload; patch: Patch }) {
  const loop = settings.research_loop
  const options = settings.research_options
  const toggle = (key: string) => {
    const next = loop.categories.includes(key)
      ? loop.categories.filter((c) => c !== key)
      : [...loop.categories, key]
    patch({ research_categories: next })
  }
  return (
    <div className="panel">
      <header>
        <h2>External research</h2>
        <div className="panel-actions">
          <button
            className={loop.enabled ? 'btn primary' : 'btn'}
            onClick={() => patch({ research_loop_enabled: !loop.enabled })}
          >
            {loop.enabled ? 'Enabled' : 'Paused'}
          </button>
        </div>
      </header>
      <div className="panel-body">
        <p className="sub">
          Searches public indexes for published claims and turns the ones this engine can
          construct a test for into open questions. A retrieved claim is a reason to ask a
          question — never evidence for an answer, and it never lowers the bar an
          experiment has to clear.
        </p>
        <div className="stack">
          <div className="budget-row">
            <span>Sources<small>Which indexes may be searched</small></span>
            <div className="chip-row">
              {options.categories.map((c) => (
                <button
                  key={c.key}
                  title={c.detail}
                  aria-pressed={loop.categories.includes(c.key)}
                  className={loop.categories.includes(c.key) ? 'btn tiny primary' : 'btn tiny'}
                  onClick={() => toggle(c.key)}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
          {loop.categories.length === 0 && (
            <p className="warning">
              No source is selected, so retrieval will find nothing. That is a valid
              choice and it is not the same as pausing research.
            </p>
          )}
          <label className="budget-row">
            <span>
              Freshness
              <small>
                {options.freshness.find((f) => f.key === loop.freshness)?.detail ?? ''}
              </small>
            </span>
            <select
              aria-label="Freshness preference"
              value={loop.freshness}
              onChange={(e) => patch({ research_freshness: e.target.value })}
            >
              {options.freshness.map((f) => (
                <option key={f.key} value={f.key}>{f.label}</option>
              ))}
            </select>
          </label>
          <label className="budget-row">
            <span>
              Depth
              <small>
                {options.depths.find((d) => d.key === loop.depth)?.detail ?? ''}
              </small>
            </span>
            <select
              aria-label="Research depth"
              value={loop.depth}
              onChange={(e) => patch({ research_depth: e.target.value })}
            >
              {options.depths.map((d) => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
          </label>
          <label className="budget-row">
            <span>Cadence<small>Minutes between bounded evidence scans</small></span>
            <input
              type="number" step="5" min="5" max="1440"
              aria-label="Research cadence"
              defaultValue={loop.interval_minutes}
              onBlur={(e) => patch({ research_interval_minutes: Number(e.target.value) })}
            />
          </label>
        </div>
      </div>
    </div>
  )
}
