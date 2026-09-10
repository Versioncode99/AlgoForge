import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api'
import { Limitations, StatusPill, type Tone } from '../components/measures'
import { PanelHead, Stat } from '../components/ui'
import { STANCE_LABEL, type Stance } from '../modes'

/* What an assistant may do here, drawn from the policy that enforces it.
 *
 * This screen is not a description of the permission model. It calls the same
 * `evaluate` the action registry calls before every AI-originated request, once
 * per registered action, and prints the ruling and the reason verbatim. A
 * permissions page that could disagree with enforcement would be worse than not
 * having one, because it would be believed.
 *
 * The three rulings are three different things and are drawn as such. Allowed
 * means it runs. Held means it goes to a person and the assistant cannot answer
 * for them. Denied means no configuration of this application permits it —
 * there is no stance, mode or setting that turns a denial into a hold.
 */

type PermissionRow = {
  action: string
  summary: string
  mutating: boolean
  risk: string
  protected: boolean
  ruling: 'allow' | 'require_approval' | 'deny'
  reason: string
}

const RULING_LABEL: Record<PermissionRow['ruling'], string> = {
  allow: 'ALLOWED',
  require_approval: 'NEEDS YOU',
  deny: 'DENIED',
}

const RULING_TONE: Record<PermissionRow['ruling'], Tone> = {
  allow: 'good',
  require_approval: 'warn',
  deny: 'bad',
}

export function ActionsView() {
  const permissions = useQuery({
    queryKey: ['mode-permissions'],
    queryFn: () =>
      getJson<{ mode: string; stance: string | null; actions: PermissionRow[] }>(
        '/modes/permissions',
      ),
  })
  const [filter, setFilter] = useState('')

  const rows = permissions.data?.actions ?? []
  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter(
      (row) =>
        row.action.includes(needle) ||
        row.summary.toLowerCase().includes(needle) ||
        row.ruling.includes(needle),
    )
  }, [rows, filter])

  const counts = rows.reduce<Record<string, number>>((totals, row) => {
    totals[row.ruling] = (totals[row.ruling] ?? 0) + 1
    return totals
  }, {})

  if (permissions.isPending) return <div className="state" role="status">Reading the policy…</div>

  return (
    <div className="fund-view af-panel-in">
      <section className="fund-top">
        <Stat label="Actions" value={rows.length} note="the whole registry" />
        <Stat label="Allowed" value={counts.allow ?? 0} tone="good" />
        <Stat label="Needs you" value={counts.require_approval ?? 0} tone="warn" />
        <Stat label="Denied" value={counts.deny ?? 0} tone="bad" />
        <Stat
          label="Stance"
          value={
            permissions.data?.stance
              ? STANCE_LABEL[permissions.data.stance as Stance]
              : 'not applicable'
          }
          note={permissions.data?.mode}
        />
      </section>

      <p className="fund-note">
        The interface and the assistant call the same registry. Every verb below is reachable from
        both, which is why there is one list rather than a UI feature set and an agent tool set that
        drift apart.
      </p>

      <section className="measure-panel">
        <PanelHead
          title="The registry"
          meta={`${shown.length} of ${rows.length}`}
        >
          <label className="actions-filter">
            <span className="sr-only">Filter actions</span>
            <input
              aria-label="Filter actions"
              placeholder="Filter by name, purpose or ruling"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
        </PanelHead>
        <table className="measure-table">
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Writes</th>
              <th scope="col">Tier</th>
              <th scope="col">For an assistant</th>
              <th scope="col">Why</th>
              <th scope="col">What it does</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.action} className="measure-row">
                <th scope="row"><span className="mono">{row.action}</span></th>
                <td className="measure-value">{row.mutating ? 'yes' : 'no'}</td>
                <td className="measure-value">
                  {row.protected ? (
                    <StatusPill label="PROTECTED" tone="bad" title="A deterministic control" />
                  ) : (
                    row.risk
                  )}
                </td>
                <td className="measure-status">
                  <StatusPill label={RULING_LABEL[row.ruling]} tone={RULING_TONE[row.ruling]} />
                </td>
                <td className="measure-detail">{row.reason}</td>
                <td className="measure-detail">{row.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <Limitations
        title="What no configuration changes"
        items={[
          'A protected action is denied to an assistant in every mode and on every stance. Risk limits, the pre-trade gate, prop rules, the kill switch, the operating mode and the stance itself are all protected.',
          'Switching to the autonomous stance is itself a protected action, so an assistant cannot grant itself the permissions that stance carries.',
          'A held action does not run and cannot be answered by the caller that proposed it. Approval arrives as a separate request from a person.',
        ]}
      />
    </div>
  )
}
