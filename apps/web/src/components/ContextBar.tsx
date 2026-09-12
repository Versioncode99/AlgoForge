import { Crosshair, FlaskConical, Layers, Wallet, X } from 'lucide-react'
import { useClearContext, useWorkstationContext, type ContextFacets } from '../workstation'

/* What is being looked at, beside what governs the screen.
 *
 * §7 is explicit that these must not be conflated: mode, workspace,
 * instrument, campaign, strategy and account are six different facts, and the
 * previous phase found the interface calling the governing workspace a mode.
 * So the workspace badge stays where it is and this is a separate group —
 * chips for the *subject*, which is a different kind of fact from the
 * permissions in force.
 *
 * Only set facets render. An empty context shows nothing rather than six
 * placeholders reading "—", because six dashes is a row of controls that look
 * broken, and a context nobody has set is not a fault.
 */

const ICON = {
  instrument: Crosshair,
  campaign: FlaskConical,
  strategy: Layers,
  account: Wallet,
} as const

const LABEL: Record<keyof typeof ICON, string> = {
  instrument: 'Instrument',
  campaign: 'Campaign',
  strategy: 'Strategy',
  account: 'Account',
}

type Facet = keyof typeof ICON

export function ContextBar() {
  const context = useWorkstationContext()
  const clear = useClearContext()

  // No workspace open, or none set. Neither is an error, and neither gets a
  // placeholder — the bar simply has nothing to say about the subject yet.
  //
  // `context` is read defensively rather than trusted. This sits in the shell
  // header, so a payload without it would take every screen down with it, and
  // "the subject is unknown" is a perfectly ordinary state to be in.
  const facets = context.data?.context
  if (!facets) return null
  const shown = (Object.keys(ICON) as Facet[]).filter(facet => facets[facet])
  if (!shown.length) return null

  return (
    <div className="context-subject" aria-label="What this workspace is looking at">
      {shown.map(facet => {
        const Icon = ICON[facet]
        const value = facets[facet]
        return (
          <span key={facet} className="context-chip" data-facet={facet}>
            <Icon aria-hidden="true" />
            <span className="sr-only">{LABEL[facet]}: </span>
            <b>{value}</b>
            {/* The timeframe rides with the instrument rather than taking a
              * chip of its own: "NQ" and "1m" are one fact on screen and two
              * in the record, and a separate chip reading "1m" means nothing
              * on its own. */}
            {facet === 'instrument' && facets.timeframe && (
              <i className="context-chip-sub">{facets.timeframe}</i>
            )}
            <button
              type="button"
              aria-label={`Clear ${LABEL[facet].toLowerCase()} context`}
              disabled={clear.isPending}
              onClick={() => clear.mutate({ facet: facet as keyof ContextFacets })}
            >
              <X aria-hidden="true" />
            </button>
          </span>
        )
      })}
    </div>
  )
}
