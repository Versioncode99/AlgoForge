import { useQuery } from '@tanstack/react-query'
import { runAction } from '../workstation'

/* Whether a *source* answered, next to whether an *archive* is sound.
 *
 * `HealthMatrix` measures datasets on disk and answers that question very
 * well. Nothing measured whether a service answered: an operator could see
 * that the NQ archive had a four-hour hole in 2019 and could not find out
 * that Databento had been refusing since lunchtime.
 *
 * Three rules this component exists to hold:
 *
 * A source nobody has called is **NOT OBSERVED**, never healthy — zero
 * failures out of zero attempts is not a clean bill of health.
 *
 * A missing credential is **UNCONFIGURED**, not failing. Nothing is wrong with
 * the service; something is missing here, and an operator sent to check
 * connectivity for an absent API key has been sent to the wrong place.
 *
 * Capabilities this build does not have are **named**, not omitted. A reader
 * who finds no news section concludes the feed is healthy and quiet.
 */

type Explain = { what: string; why: string; impact: string; remedy: string }

type ServiceRow = {
  name: string
  tier: string
  state: 'NOT_OBSERVED' | 'HEALTHY' | 'DEGRADED' | 'FAILING' | 'UNCONFIGURED'
  ok: number
  failed: number
  last_latency_ms: number | null
  last_success: string | null
  observed: string
  explain: Explain
}

type AbsentRow = { capability: string; state: string } & Explain

type CalendarRow = {
  provider: string
  available: boolean
  reason: string
  state: string
  attribution?: string
}

type TierRow = { tier: string; rank: number; means: string }

type Health = {
  services: ServiceRow[]
  calendars: CalendarRow[]
  absent: AbsentRow[]
  tiers: TierRow[]
}

const TONE: Record<string, string> = {
  HEALTHY: 'is-passed',
  DEGRADED: 'is-reserved',
  FAILING: 'is-failed',
  UNCONFIGURED: 'is-reserved',
  NOT_OBSERVED: 'is-unknown',
}

const LABEL: Record<string, string> = {
  NOT_OBSERVED: 'NOT OBSERVED',
  UNCONFIGURED: 'NOT CONFIGURED',
}

function Row({ name, tier, state, detail, explain }: {
  name: string; tier: string; state: string; detail: string; explain: Explain
}) {
  return (
    <details className="service-row">
      <summary>
        <span className={`status-badge ${TONE[state] ?? 'is-unknown'}`}>{LABEL[state] ?? state}</span>
        <strong>{name}</strong>
        <span className="service-tier mono">{tier}</span>
        <small>{detail}</small>
      </summary>
      <dl className="service-explain">
        <dt>What</dt><dd>{explain.what}</dd>
        <dt>Why</dt><dd>{explain.why}</dd>
        <dt>Impact</dt><dd>{explain.impact}</dd>
        {explain.remedy && <><dt>Remedy</dt><dd>{explain.remedy}</dd></>}
      </dl>
    </details>
  )
}

export function ServiceHealth() {
  const query = useQuery({
    queryKey: ['data-health-services'],
    queryFn: () => runAction<Health>('data_health'),
    refetchInterval: 30_000,
  })

  if (query.isPending) return <div className="state" role="status">Checking sources…</div>
  if (query.isError) {
    return (
      <div className="state error" role="alert">
        Source health is unavailable: {query.error.message}
      </div>
    )
  }

  const { services, calendars, absent, tiers } = query.data
  return (
    <div className="service-health">
      <section>
        <h4>Sources</h4>
        {services.map(service => (
          <Row
            key={service.name}
            name={service.name}
            tier={service.tier}
            state={service.state}
            detail={
              service.state === 'NOT_OBSERVED'
                ? 'no calls yet this session'
                : `${service.ok} ok · ${service.failed} failed${service.last_latency_ms !== null ? ` · ${Math.round(service.last_latency_ms)}ms` : ''} · ${service.observed}`
            }
            explain={service.explain}
          />
        ))}
      </section>

      <section>
        <h4>Economic calendar</h4>
        {calendars.map(calendar => (
          <Row
            key={calendar.provider}
            name={calendar.provider}
            tier="RESEARCH_GRADE"
            state={calendar.state}
            detail={calendar.reason}
            explain={{
              what: `${calendar.provider} is ${calendar.available ? 'readable' : 'not readable'}.`,
              why: calendar.reason,
              impact: calendar.available
                ? 'Scheduled releases from this source are available to blackout policy and research.'
                : 'Its events are absent. A blackout configured for them cannot be confirmed either way.',
              remedy: calendar.available ? '' : 'Supply what it needs, or record events manually.',
            }}
          />
        ))}
      </section>

      {/* Named, not omitted. */}
      <section>
        <h4>Not available in this build</h4>
        {absent.map(row => (
          <Row
            key={row.capability}
            name={row.capability}
            tier="UNAVAILABLE"
            state="UNCONFIGURED"
            detail={row.what}
            explain={row}
          />
        ))}
      </section>

      <section className="tier-ladder">
        <h4>What each tier may be used for</h4>
        <dl>
          {tiers.map(tier => (
            <div key={tier.tier}>
              <dt className="mono">{tier.tier}</dt>
              <dd>{tier.means}</dd>
            </div>
          ))}
        </dl>
        <p>
          A source below the tier a consumer asked for is blocked, not substituted.
          Informational panels may show a degraded or stale value with its marker;
          a result may not rest on one.
        </p>
      </section>
    </div>
  )
}
