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
      <defs>
        <linearGradient id="af-steel" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8b959d" />
          <stop offset="1" stopColor="#4d555c" />
        </linearGradient>
        <filter id="af-glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="1.4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <rect x="4" y="4.4" width="24" height="3.2" rx="1.6" fill="url(#af-steel)" opacity=".95" />
      <rect x="6.5" y="10.8" width="19" height="3.2" rx="1.6" fill="url(#af-steel)" opacity=".72" />
      <rect x="9" y="17.2" width="14" height="3.2" rx="1.6" fill="url(#af-steel)" opacity=".5" />
      <rect x="11.5" y="23.6" width="9" height="3.2" rx="1.6" fill="#3ddc97" filter="url(#af-glow)" />
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
