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

export function Wordmark() {
  return (
    <div className="brand">
      <Mark title="AlgoForge" />
      <div className="brand-text">
        <span className="brand-name">ALGOFORGE</span>
        <span className="brand-sub">PAPER ONLY</span>
      </div>
    </div>
  )
}
