import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getJson } from '../api'

/* How large is the construction vocabulary, and what is in it?
 *
 * The last audit could only answer that by reading the source, and the answer it
 * found was the bottleneck: ten fixed signals, whatever else varied. A campaign
 * exhausted them and then spent its remaining cycles being refused for
 * proposing them again — which read, on the old screen, as the engine working.
 *
 * So the number is served rather than asserted, and the shape of it is shown
 * too. `distinct_triggers` is how many structurally different signals the
 * grammar can assemble; `reachable_signatures` multiplies that by the regime
 * gates each one can carry. Neither is a promise that they are all worth
 * running, and the panel says so: a reachable set is a ceiling, not a result.
 */

export type VocabularyPayload = {
  primitives: {
    total: number
    observations: number
    transformations: number
    by_category: Record<string, number>
    rows: {
      kind: string; label: string; arity: number; source: string
      category: string; unit: string; description: string; data_requirement: string
    }[]
  }
  mechanisms: {
    key: string; label: string; stance: string; claim: string; prediction: string
    requires: string[]; on_failure: string; data: string[]; reads: string
  }[]
  grammar: {
    observables: number; transformations: number; shapes: number
    gate_candidates: number; triggers_per_shape: Record<string, number>
    distinct_triggers: number; reachable_signatures: number; categories: string[]
  }
  written_archetypes: number
  note: string
}

export function useVocabulary() {
  return useQuery({
    queryKey: ['vocabulary'],
    staleTime: 60 * 60 * 1000,
    queryFn: () => getJson<VocabularyPayload>('/campaigns/vocabulary'),
  })
}

function Count({ label, value, note }: { label: string; value: number; note: string }) {
  return (
    <div className="metric" title={note}>
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
      <small>{note}</small>
    </div>
  )
}

export function VocabularyPanel() {
  const [open, setOpen] = useState(false)
  const query = useVocabulary()
  const data = query.data
  // Guarded on the *shape* rather than on presence. A response that arrives but
  // is missing a section — an older server, a partial parse — would otherwise
  // take down the whole Research Control Center over a panel that is not the
  // reason anybody opened it.
  if (!data?.primitives || !data.grammar || !Array.isArray(data.mechanisms)) {
    return (
      <section className="rcc-accounting">
        <h3>Construction vocabulary</h3>
        <p className="rcc-note">
          {query.isError
            ? 'The vocabulary could not be read.'
            : query.isPending
              ? 'Reading…'
              : 'This server did not report a construction vocabulary.'}
        </p>
      </section>
    )
  }

  const { primitives, grammar, mechanisms } = data
  return (
    <section className="rcc-accounting">
      <h3>Construction vocabulary</h3>
      <p className="rcc-note">
        What a signal can be built out of. A construction is either one of the{' '}
        {data.written_archetypes} written archetypes or one the grammar assembled from
        these primitives — there is no third kind, and nothing here can express a
        computation the catalogue does not already implement and test.
      </p>
      <div className="rcc-metrics">
        <Count
          label="Feature primitives"
          value={primitives.total}
          note={`${primitives.observations} computed from the bars, ${primitives.transformations} computed from another feature's own history.`}
        />
        <Count
          label="Mechanisms"
          value={mechanisms.length}
          note="Each carries a falsifiable prediction and what a failure would rule out."
        />
        <Count
          label="Signal shapes"
          value={grammar.shapes}
          note="Structural forms a trigger can take. Each one is directional and has a real mirror."
        />
        <Count
          label="Distinct triggers"
          value={grammar.distinct_triggers}
          note="Structurally different signals the grammar can assemble, before any regime gate."
        />
        <Count
          label="Regime gates"
          value={grammar.gate_candidates}
          note="Scale-free readings that can restrict when a trigger counts."
        />
        <Count
          label="Reachable constructions"
          value={grammar.reachable_signatures}
          note="Triggers times the gates each can carry. A ceiling on what could be explored, not a claim that all of it is worth running."
        />
      </div>

      <button className="btn tiny" onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide the catalogue' : 'Show the catalogue'}
      </button>

      {open && (
        <div className="stack">
          <h4 className="sub">Primitives by category</h4>
          <table className="tbl">
            <tbody>
              {Object.entries(primitives.by_category).map(([category, count]) => (
                <tr key={category}>
                  <td>{category}</td>
                  <td className="mono">{count}</td>
                  <td className="muted">
                    {primitives.rows
                      .filter((r) => r.category === category)
                      .map((r) => r.label)
                      .join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4 className="sub">Triggers by shape</h4>
          <table className="tbl">
            <tbody>
              {Object.entries(grammar.triggers_per_shape).map(([shape, count]) => (
                <tr key={shape}>
                  <td>{shape.replaceAll('_', ' ')}</td>
                  <td className="mono">{count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4 className="sub">Mechanisms</h4>
          <table className="tbl">
            <thead>
              <tr><th>Mechanism</th><th>Stance</th><th>What would abandon it</th></tr>
            </thead>
            <tbody>
              {mechanisms.map((m) => (
                <tr key={m.key}>
                  <td>
                    <strong>{m.label}</strong>
                    <div className="muted small">{m.claim}</div>
                  </td>
                  <td className="mono">{m.stance}</td>
                  <td className="muted">{m.prediction.replace('{observable}', 'the observation')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
