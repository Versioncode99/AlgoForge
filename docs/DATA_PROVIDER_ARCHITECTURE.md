# Data Provider Architecture

Where a value came from, what it is good enough for, how old it is, and whether
it may be evidence.

Source: `packages/forge/data/freshness.py`, `packages/forge/data/services.py`,
`packages/forge/data/providers.py`, the `data_health` action, and
`apps/web/src/components/ServiceHealth.tsx`.

---

## What was already here, and what was missing

`packages/forge/data/health.py` measures **a dataset on disk**: rows, duplicate
and out-of-order timestamps, in-session gaps classified against the daily
maintenance break and the weekend, OHLC consistency, non-positive prices. Every
number is arithmetic over the archive, severity comes from the *share* of open
time missing rather than an absolute hour count, and the exclusions are reported
so the classification can be audited. It is a good answer to a hard question.

`ProviderDescriptor` carried `authority` — TRUTH / CONTEXT / FIXTURE — and
`Dataset` used it, so a synthetic set could never clear the judge's data gate.

**Nothing measured whether a service answered.** An operator could see that the
NQ archive had a four-hour hole in 2019 and could not find out that Databento
had been refusing since lunchtime. There was also no freshness model at all, and
no way for a consumer to say what quality it required.

## Tiers

`Tier` extends the `authority` axis rather than introducing a competing one:
TRUTH splits into what a backtest may rest on and what may only be looked at.

| Tier | Means | Rank |
|---|---|---|
| `EXECUTION_GRADE` | Licensed, timestamped, revision-documented. Fit to size an order from. | 4 |
| `RESEARCH_GRADE` | Reproducible and provenanced, but not a venue feed. Fit for a backtest. | 3 |
| `INDICATIVE` | Public or undocumented endpoints. Fit to look at, not to conclude from. | 2 |
| `SYNTHETIC` | Generated. Fit for testing the machinery, never for a finding. | 1 |
| `UNAVAILABLE` | Nothing answered. | 0 |

The vocabulary is about **use**, not price or vendor. A free public endpoint and
a paid one can both be INDICATIVE, and calling either "institutional" because an
invoice exists is the mistake the enum exists to make unavailable.

**Nothing in this build is `EXECUTION_GRADE`**, and `test_nothing_in_this_build_claims_to_be_execution_grade`
asserts it. There is no venue feed and no order path.

Current assignment (`PROVIDER_TIERS`): databento and fred RESEARCH_GRADE,
crypto-public INDICATIVE, local-fixture SYNTHETIC.

## Freshness, and why it is in the type

OpenTerminal's cache:

```ts
} catch (err) {
  const stale = staleGet<T>(key);
  if (stale !== undefined) return stale;
  throw err;
}
```

On provider failure the last-known value comes back and **the caller cannot
tell** — `cached<T>()` returns `T` whether the data is one second old or one
week old. For a quote panel that is correct; a slightly old price beats a blank
panel. For a backtest it is a fabricated result.

So the mechanism is kept and the silence is removed. `Served[T]` carries the
value, the provider, the tier, the freshness, the retrieval time, and the
instant the data describes (`as_of`, `None` when the source did not say — which
is not the same as "now").

| Freshness | Means |
|---|---|
| `FRESH` | Inside its TTL. |
| `REFRESHING` | Past its TTL, a refresh is under way. |
| `STALE` | Past its TTL, the refresh failed. |
| `DEGRADED` | Served by a lower tier than was asked for. |
| `UNAVAILABLE` | Nothing to serve; there is no value. |

`REFRESHING` and `STALE` are separate because "wait a moment" and "the source is
down" are different things to show a reader.

## Admissibility

```python
ok, reason = served.admissible(Tier.RESEARCH_GRADE)
```

Returns the answer **and the reason**, because a caller that is refused has to
be able to tell an operator what to do about it, and `False` on its own sends
them to look at the wrong thing.

Three rules, each a test:

1. Nothing returned is never admissible, however high the tier.
2. A lower tier is **blocked, not warned**. Substituting changes what the result
   means, so a backtest that declared EXECUTION_GRADE and is handed INDICATIVE
   does not run with a footnote.
3. `STALE` and `DEGRADED` are usable for a panel and not for a result. That
   distinction is the whole reason freshness and tier are separate fields —
   §12 permits an informational surface to render a cached value with its
   marker and does not permit a backtest to rest on one.

## Fallback chains

`chain(attempts, required=, registry=)` is `withFallback` with the silence
removed. OpenTerminal's returns the first success and the caller never learns
which link answered; here the descent is reported:

- The order is the order given — deterministic, so the same outage produces the
  same descent twice, and observable, so an operator can see it happened.
- The serving provider and tier come back on the envelope.
- A link answering **below** `required` is marked `DEGRADED` and carries the
  reason, so it renders on a chart and is refused for a result.
- A descent *within* a tier is `FRESH`, and the earlier failures are still
  carried in `reason` so the descent stays observable.
- Nothing left returns `UNAVAILABLE` with every provider's error.

## Service health

`ServiceRegistry` is OpenTerminal's `providers/registry.ts` with two changes
that matter for a system producing evidence.

**A service nobody has called is `NOT_OBSERVED`, never healthy.** Zero failures
out of zero attempts is not a clean bill of health — the same error as a dossier
reporting zeroes for a strategy that was never validated. Services are
`declare`d up front so a health panel can show that the one you are waiting on
has never been tried, rather than omitting it.

**A missing credential is `UNCONFIGURED`, not `FAILING`.** Nothing is wrong with
the service; something is missing here, and an operator sent to check
connectivity for an absent API key has been sent to the wrong place.
`credential_present()` answers one bit and returns nothing else.

States: `NOT_OBSERVED`, `HEALTHY`, `DEGRADED` (answering, with failures),
`FAILING` (three consecutive failures), `UNCONFIGURED`.

Counters are **observations since this process started**, and every row says so.
They are not uptime, they do not survive a restart, and a surface presenting
them as uptime would be inventing history.

Every row carries WHAT / WHY / IMPACT / REMEDY. A status light with no remedy is
decoration.

## What this build does not have

`ABSENT_CAPABILITIES` names them rather than omitting them, because an absent
row reads as "fine" — a reader who finds no news section concludes the feed is
healthy and quiet.

- **Headline news.** There is no headline feed in this codebase. The module
  named `news` models *scheduled economic events*. Nothing is fabricated to
  fill the gap, and none ships.
- **Live quotes and depth.** Paper-only; no live-order path exists anywhere.
- **Execution-grade data.** No source here qualifies.

## Data Health

The `data_health` action composes five sections: the measured dataset matrix,
the service registry, calendar source availability (from the existing
`CalendarRegistry`, which already refuses rather than falling back when
`FRED_API_KEY` is absent), the absent capabilities, and the tier ladder with
what each tier means.

Rendered by `ServiceHealth.tsx` beside the existing `HealthMatrix`, so an
archive's soundness and its source's availability are finally on one screen.

Measured output on a machine with no credentials: databento and fred
`UNCONFIGURED`, crypto-public and local-fixture `NOT_OBSERVED`, manual calendar
`HEALTHY`, FRED calendar `UNCONFIGURED` with the URL that issues free keys.

## Honest limitation

**Nothing consumes `Served` on the market-data read path yet.** `MarketService`
still returns bare frames; `/bars` still 409s honestly when a dataset is not
downloaded. The envelope, the chain, the tier ladder and the health registry are
built, wired into `data_health`, and tested — but until a reader takes a
`Served` and calls `admissible()` before using the bars, the *enforcement* half
of this document describes a capability rather than a behaviour in force. That
is recorded here and in the final report rather than counted as finished.
