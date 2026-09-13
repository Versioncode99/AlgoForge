import type { ControlCenter } from '../research'

/* Is an agent doing research, or producing activity?
 *
 * The registry counts experiments; the skip ledger counts refusals. An agent
 * that proposed forty things and had thirty-eight refused has an experiment
 * count that looks like work, and until the two halves are shown together
 * nothing on the screen can say otherwise.
 *
 * Three deliberate restraints. A role that has done nothing is not shown, so a
 * column of zeroes does not read as a measurement. An unmetered efficiency says
 * "not measured" rather than showing 0.0, which reads as inefficient. And a
 * weakest role is named only once it has proposed enough for the number to mean
 * something — a league table of roles that proposed twice each is not a finding.
 */

type Effectiveness = ControlCenter['effectiveness']

export function AgentEffectiveness({ data }: { data: Effectiveness | undefined }) {
  if (!data?.roles?.length) {
    return (
      <section className="rcc-accounting">
        <h3>Agent effectiveness</h3>
        <p className="rcc-note">
          No agent has proposed anything yet. Acceptance is measured from proposals,
          so there is nothing to measure rather than a score of zero.
        </p>
      </section>
    )
  }

  return (
    <section className="rcc-accounting">
      <h3>Agent effectiveness</h3>
      <p className="rcc-note">{data.note}</p>
      <table className="tbl">
        <thead>
          <tr>
            <th>Role</th>
            <th>Proposals</th>
            <th>Became experiments</th>
            <th>Refused</th>
            <th>Nothing to do</th>
            <th>Findings</th>
            <th>Acceptance</th>
            <th>Efficiency</th>
          </tr>
        </thead>
        <tbody>
          {data.roles.map((role) => (
            <tr
              key={role.role}
              className={role.role === data.weakest_role ? 'is-weak' : undefined}
            >
              <td><strong>{role.role}</strong><small className="muted"> · {role.agents}</small></td>
              <td className="mono">{role.proposals}</td>
              <td className="mono">{role.experiments}</td>
              <td className="mono">{role.refusals}</td>
              <td className="mono">{role.idle_cycles}</td>
              <td className="mono">{role.findings}</td>
              <td className="mono">{(role.acceptance * 100).toFixed(0)}%</td>
              <td className="mono muted">
                {role.efficiency_measured ? role.efficiency.toFixed(2) : 'not measured'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.weakest_role && (
        <p className="rcc-note rcc-note-tight">
          <b>{data.weakest_role}</b> is accepting {(data.weakest_acceptance * 100).toFixed(0)}% of
          its proposals — the lowest of any role that has proposed enough for the figure to
          mean anything. Usually that corner of the frontier is exhausted rather than the
          agent being faulty.
        </p>
      )}
    </section>
  )
}
