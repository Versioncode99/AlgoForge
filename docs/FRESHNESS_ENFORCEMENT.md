# Where the freshness envelope is enforced, and where it is not

D1 §35 asks for a determination rather than a rollout:

> *"A freshness abstraction that isn't enforced is not a real contract. But do
> not add it to every subsystem indiscriminately. Only enforce where freshness
> actually matters."*

So this is the audit of the seven consumers it names, with what was done to each
and why. It is written against the code rather than against the intention.

## What the abstraction is

`packages/forge/data/freshness.py` puts a value in a `Served` envelope carrying
the provider that answered, the **tier** it answers at, how **fresh** it is, and
when it was retrieved. The rule that makes it a contract rather than a label is
in its own docstring: *"Crossing down a tier blocks the consumers that declared
the higher one; it does not warn them."*

`packages/forge/data/services.py` is the other half: whether a source answered,
how fast, and whether a descent through a fallback chain crossed a tier.

## The seven, decided

| Consumer | Decision | What is actually there |
| --- | --- | --- |
| **Market data** | **Enforced** | `services.chain` marks a fallback that crosses a tier `DEGRADED`, so a consumer that declared the higher tier is blocked rather than warned. `ServiceRegistry` starts every source at `NOT_OBSERVED` — never healthy — so an untried provider cannot read as fine. |
| **Cached data** | **Enforced, by construction** | A cached value is returned inside `Served` with its own `retrieved_at`, which is the entire reason the envelope exists: OpenTerminal's cache returns `T` whether the value is a second or a week old, and the caller cannot tell. |
| **External sources** | **Enforced** | Every retrieved source carries `retrieved_at`, a content hash taken at retrieval, and a report of what sanitising removed. See `packages/forge/research/untrusted.py`. A source stored before hashing existed reads as *unverifiable* rather than as tampered. |
| **Charts** | **Labelled, deliberately not gated** | The chart now reports how old the archive is — `apps/web/src/freshness.ts`, rendered beside the OHLC readout. It does **not** block. AlgoForge draws local archives, so a chart is never stale in the market-data sense; what it can be is *old*, and reading the right-hand edge as "now" is the mistake. The archive already reported `coverage_end`, `bars.ts` carried it, and nothing rendered it. |
| **Research** | **Enforced where it decides something** | `forge.research.plan` reads `NOT_EVIDENCE` when deciding what a plan may rest on. A claim whose freshness class is not evidence cannot become evidence by being cited. |
| **Agent inputs** | **Enforced at the boundary, not per field** | An action result that carries retrieved text is fenced before it reaches a prompt (`Action.external`, `Assistant._for_prompt`). The tier ladder reaches an agent through `data_health`, which reports what each source may be evidence for. |
| **Workstation panels** | **Not enforced, on purpose** | A panel renders what a route returned. Putting an envelope in front of every panel would mean a freshness badge on a strategy list and a panel count, which is where an indiscriminate rollout goes: an indicator on everything is an indicator nobody reads. Panels that show *market* data reach it through the chart path above, which does carry the reading. |

## What is deliberately not blocked

**The chart.** Gating a chart on archive age would stop somebody looking at 2019
in an archive that ends in 2019, which is the normal case for research. The
label carries the warning and the operator keeps the capability.

**The backtest.** A backtest over an old archive is not wrong — it is a
measurement of that period, and the evidence tier already says what it may be
used for. What would be wrong is a *verdict* presented as current, and G0 already
refuses a run whose data receipt fails.

## What this changed

One thing, and it was real: the chart knew how old its archive was and never
said. `coverage_end` travelled from the route into `bars.ts` and stopped there.
`archiveAge` turns it into a reading with three bands and an explicit
**unknown** — because an archive that did not report its newest bar is not the
same as one that is current, and a blank badge would have read as the latter.

## What this did not change

No new envelope was threaded through a subsystem that did not already have one.
The directive's own warning is the reason: an abstraction applied everywhere
stops being a signal. The determination above is the deliverable §35 asks for,
and the one wiring is the place where the answer was "yes, and it is missing".
