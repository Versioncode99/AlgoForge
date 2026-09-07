import { useEffect, useRef, useState } from 'react'

/** A number that rolls to its new value instead of snapping.
 *
 * Snapping makes a changing figure read as a fresh render; rolling makes it
 * read as the same quantity moving, which is what it is. Kept short so it never
 * lags behind a fast poll, and skipped entirely when the jump is trivial.
 */
export function Rolling({
  value,
  decimals = 0,
  prefix = '',
  suffix = '',
  className,
}: {
  value: number
  decimals?: number
  prefix?: string
  suffix?: string
  className?: string
}) {
  const [shown, setShown] = useState(value)
  // Direction is information a rolling number loses: by the time the digits
  // settle, "went up" and "went down" look identical. The tint is dropped as
  // soon as it has been read, so a static screen carries no colour.
  const [tick, setTick] = useState<'' | 'af-tick-up' | 'af-tick-down'>('')
  const from = useRef(value)
  const frame = useRef<number | null>(null)

  useEffect(() => {
    if (value === from.current) return
    setTick(value > from.current ? 'af-tick-up' : 'af-tick-down')
    const clear = window.setTimeout(() => setTick(''), 560)
    return () => window.clearTimeout(clear)
  }, [value])

  useEffect(() => {
    const start = from.current
    const delta = value - start
    if (Math.abs(delta) < 10 ** -decimals) {
      setShown(value)
      from.current = value
      return
    }
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setShown(value)
      from.current = value
      return
    }
    const began = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / 380)
      // Out-quint, matching --ease-out so motion feels like one system.
      const eased = 1 - (1 - t) ** 5
      setShown(start + delta * eased)
      if (t < 1) frame.current = requestAnimationFrame(step)
      else from.current = value
    }
    frame.current = requestAnimationFrame(step)
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current)
      from.current = value
    }
  }, [value, decimals])

  return (
    <span className={[className, tick].filter(Boolean).join(' ') || undefined}>
      {prefix}
      {shown.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {suffix}
    </span>
  )
}

/** One labelled figure with the reason it matters underneath. */
export function Stat({
  label,
  value,
  note,
  tone = 'plain',
  wide,
}: {
  label: string
  value: React.ReactNode
  note?: string
  tone?: 'plain' | 'good' | 'bad' | 'unknown'
  wide?: boolean
}) {
  return (
    <div className="stat-tile" data-tone={tone} data-wide={wide ? 'yes' : undefined}>
      <span className="stat-label">{label}</span>
      <b className="stat-value">{value}</b>
      {note && <p className="stat-note">{note}</p>}
    </div>
  )
}

const VERDICT_LABEL: Record<string, string> = {
  PASS: 'PASS',
  FAIL: 'FAIL',
  INCONCLUSIVE: 'NOT MEASURED',
}

/** PASS / FAIL / NOT MEASURED, as three visibly different things. */
export function VerdictPill({ status, title }: { status: string; title?: string }) {
  const tone = status === 'PASS' ? 'good' : status === 'INCONCLUSIVE' ? 'unknown' : 'bad'
  return (
    <span className="verdict-pill" data-tone={tone} title={title}>
      {VERDICT_LABEL[status] ?? status}
    </span>
  )
}

const TIER_TONE: Record<string, string> = {
  HOLDOUT: 'holdout',
  TRUTH_OOS: 'oos',
  VALIDATION_OOS: 'oos',
  FORWARD: 'oos',
  DEVELOPMENT_IN_SAMPLE: 'insample',
  LEGACY_IN_SAMPLE: 'insample',
  SYNTHETIC: 'synthetic',
}

const TIER_LABEL: Record<string, string> = {
  HOLDOUT: 'HOLDOUT',
  TRUTH_OOS: 'OUT OF SAMPLE',
  VALIDATION_OOS: 'OUT OF SAMPLE',
  FORWARD: 'FORWARD',
  DEVELOPMENT_IN_SAMPLE: 'IN SAMPLE',
  LEGACY_IN_SAMPLE: 'IN SAMPLE',
  SYNTHETIC: 'SYNTHETIC',
}

/** Evidence tier is ranked, so it carries a colour before it carries a word. */
export function TierPill({ tier }: { tier?: string | null }) {
  if (!tier) return <span className="tier-pill" data-tone="synthetic">UNTESTED</span>
  return (
    <span className="tier-pill" data-tone={TIER_TONE[tier] ?? 'synthetic'}>
      {TIER_LABEL[tier] ?? tier}
    </span>
  )
}

/** An empty state that names the next action rather than the absence.
 *
 * "No backtest yet" tells the user something they already know. The useful
 * version says what to press and why it is needed.
 */
export function Empty({
  title,
  detail,
  action,
}: {
  title: string
  detail: string
  action?: React.ReactNode
}) {
  return (
    <div className="empty-state af-panel-in">
      <b>{title}</b>
      <p>{detail}</p>
      {action}
    </div>
  )
}

/** Section heading with an optional right-hand slot. */
export function PanelHead({
  title,
  meta,
  children,
}: {
  title: string
  meta?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <header className="panel-head">
      <h2>{title}</h2>
      {meta && <span className="panel-meta">{meta}</span>}
      {children}
    </header>
  )
}
