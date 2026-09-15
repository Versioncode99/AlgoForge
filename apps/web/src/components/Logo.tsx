/** The AlgoForge mark: a gate funnel.
 *
 * Four bars descending in width — many candidates enter, one survives. The
 * surviving bar is the only lit element, which is the whole thesis of the system
 * rendered at 22 pixels. */
export function Mark({ size = 22, title }: { size?: number; title?: string }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <rect x="4" y="4.4" width="24" height="3.2" rx="1.6" fill="#8b857a" opacity=".95" />
      <rect x="6.5" y="10.8" width="19" height="3.2" rx="1.6" fill="#8b857a" opacity=".72" />
      <rect x="9" y="17.2" width="14" height="3.2" rx="1.6" fill="#8b857a" opacity=".5" />
      <rect x="11.5" y="23.6" width="9" height="3.2" rx="1.6" fill="#c58a5a" />
    </svg>
  )
}

/** What "PAPER ONLY" means, in one sentence.
 *
 * Exported so the test asserting the claim survives reads the same string the
 * interface shows, rather than a copy of it that can drift.
 */
export const BOUNDARY =
  'AlgoForge has no broker, venue or order-routing connector. Every number here ' +
  'comes from a backtest or a local simulator, never from a live account.'

export function Wordmark() {
  return (
    <div className="brand">
      <Mark title="AlgoForge" />
      <div className="brand-text">
        <span className="brand-name">ALGOFORGE</span>
        {/* The label is on every screen; the sentence explaining it is not
            visible anywhere else.

            It used to be on the mode chooser — "no broker, venue or
            order-routing vendor is connected" — and the chooser is gone. Two
            words on their own read as a setting somebody could turn off, which
            is the opposite of what they mean: this build has no broker
            connector at all. So the words carry their explanation, to a screen
            reader always and to a pointer on hover. */}
        <span
          className="brand-sub"
          title={BOUNDARY}
        >PAPER ONLY</span>
        <span className="sr-only">{BOUNDARY}</span>
      </div>
    </div>
  )
}
