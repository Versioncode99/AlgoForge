import { useMemo, useState } from 'react'
import { FanBands } from '../charts'
import { clampDay, describe, readingAt, type FanPoint } from '../fan'
import { money, pct } from '../lib'

/** A row of the readout. Kept tiny on purpose: the point of the readout is the
 *  numbers lining up, not the frame around them. */
function Cell({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={strong ? 'fan-cell fan-cell-strong' : 'fan-cell'}>
      <span className="fan-cell-label">{label}</span>
      <span className="mono fan-cell-value">{value}</span>
    </div>
  )
}

/** The scrubbable equity fan.
 *
 * Two things make this different from the path plot beside it. The band is
 * computed over *every* simulated account rather than the 100 carried back for
 * inspection, and it can be read at a chosen day instead of only as a shape.
 *
 * The scrubber is a range input rather than hover alone, deliberately: hover
 * cannot be reached from a keyboard, cannot be held still while reading, and
 * does not survive a screenshot. Hovering the chart moves the same control, so
 * there is one day selected and one place it is stored.
 */
export function EquityFan({ points, start }: { points: FanPoint[]; start: number }) {
  const last = points.length ? points[points.length - 1].day : 0
  const [day, setDay] = useState(last)
  const selected = clampDay(points, day)
  const reading = useMemo(() => readingAt(points, selected, start), [points, selected, start])

  if (!reading) {
    return <p className="muted">No fan: this simulation produced no days to band.</p>
  }

  const { point } = reading
  return (
    <div className="fan">
      <FanBands points={points} marked={selected} onScrub={(d) => setDay(clampDay(points, d))} />

      <label className="fan-scrub">
        <span className="fan-scrub-label">Day</span>
        <input
          type="range"
          min={points[0].day}
          max={last}
          step={1}
          value={selected}
          aria-label="Day to read the distribution at"
          aria-valuetext={`Day ${selected} of ${last}`}
          onChange={(event) => setDay(Number(event.target.value))}
        />
        <output className="mono fan-scrub-day">{selected}</output>
      </label>

      <div className="fan-readout" role="group" aria-label={`Distribution on day ${selected}`}>
        <Cell label="p05" value={money(point.p05)} />
        <Cell label="p25" value={money(point.p25)} />
        <Cell label="median" value={money(point.median)} strong />
        <Cell label="p75" value={money(point.p75)} />
        <Cell label="p95" value={money(point.p95)} />
        <Cell label="spread" value={money(reading.spread)} />
        <Cell label="still trading" value={`${point.live} · ${pct(reading.stillTrading)}`} />
      </div>

      <p className="fan-note">{describe(reading)}</p>
    </div>
  )
}
