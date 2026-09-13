# Shared data and window visibility

The trace Doc 2 §16–§18 asks for, what it found, and the one thing that needed
building. Written after tracing rather than before, because the directive is
explicit that the architecture must be *verified* rather than asserted by
creating classes.

## The path, end to end

```
BrowserWindow (main process)
  └─ WindowRegistry          window id → workspace id, group, bounds, visibility
       └─ renderer (its own process, its own QueryClient)
            └─ Workspace → Panel → useQuery(key)
                 └─ GET /bars?dataset=…&timeframe=…&before=…
                      └─ ChartBars._memo[dataset.key]      in-process, shared
                           └─ MarketDataCache → provider
```

Two things are worth being precise about, because the obvious reading of "five
windows on NQ" is wrong in both directions.

## What is already shared, and where

**Bars are shared at the process that owns them.** `ChartBars._memo` is keyed by
dataset, and `MarketDataCache` sits under it. Five windows requesting NQ 1m
produce five HTTP requests and **one** provider fetch — the second through fifth
are served from memory. So the duplication Doc 2 §16 warns about
("do not automatically create five independent market-data pipelines") does not
exist at the pipeline level. There is one pipeline; there are several readers of
it.

**Within one window, React Query already deduplicates.** Two chart panels on the
same instrument share a query key and therefore share one request and one cache
entry. `staleTime: 30_000` means a remount inside that window does not refetch.

**Across windows it does not, and cannot.** Each Electron window is its own
renderer process with its own `QueryClient`. That is a property of the
architecture, not a defect in it, and the cost it actually imposes is *polling*
— not bar fetching, which the server-side cache already absorbs.

## What was actually missing

Not a data fabric. The gap was narrower, and in one specific place.

`@tanstack/query-core` gates every interval refetch on
`focusManager.isFocused()`, which is `document.visibilityState !== 'hidden'`
(`queryObserver.js`: `if (this.options.refetchIntervalInBackground ||
focusManager.isFocused()) this.#executeFetch()`). Chromium marks a minimised
window's document hidden, so **a minimised window already stops polling** and
§18 is satisfied for that case by the framework, for free.

What the framework cannot see is the state between: a window **fully covered by
another** reports `visible` and keeps polling at full rate. With roughly twenty
interval queries in the application, several at 1.5–2.5 s, that is real work
done for a surface nobody can read.

## The state that must *not* be acted on

The tempting fix — pause polling when a window loses focus — would be wrong, and
wrong in the direction that matters.

A trading workstation is several windows open **and watched** at once. A risk
panel beside a chart is the arrangement Doc 2 §4 exists to support; exactly one
of them can be frontmost, and pausing the other would make the product worse to
save requests nobody was short of. Doc 2 §15 says this directly: do not optimise
by making the product less capable.

So visibility has three states and only one of them stands a renderer down:

| State | Meaning | Periodic work |
| --- | --- | --- |
| `active` | On screen, frontmost | Continues |
| `background` | On screen, not frontmost | **Continues** |
| `obscured` | Minimised, hidden, or fully covered | Stops |

## How it is wired

The main process is the only party that knows the difference between covered and
merely-unfocused, so it is the source. `WindowRegistry.setVisibility()` records
it and returns whether the state actually moved, so a window shown while already
shown does not wake its renderer for nothing.

The renderer feeds `obscured` into **the query layer's own focus gate**
(`focusManager.setFocused`) rather than a second mechanism beside it. That is the
whole of the change: one existing gate, given a signal it could not otherwise
obtain. No broker, no second cache, no parallel subscription system — Doc 2 §28
forbids a parallel system, and there was no gap here large enough to justify one.

The channel runs main → renderer only and is deliberately **not** in
`CHANNEL_NAMES`, the set a renderer may invoke. Keeping it out means the invoke
allowlist stays exactly what it claims to be.

## Safety does not depend on any of this

Doc 2 §18's second half is the load-bearing one: *never make safety depend on
whether a renderer is visible.* It holds by construction here, because nothing
being paused is a safety mechanism. Risk limits, account monitoring, prop rules
and the pre-trade gate are evaluated in the backend, which does not know whether
a window exists at all. What stops when a window is obscured is the *drawing* of
numbers, not the computing of them.

## What this deliberately leaves for measurement

Several *visible* windows still poll the same global endpoints (`/health`,
`/activity`, engine state) independently. Deduplicating that would need the main
process to broker requests — a real parallel data path, for a saving currently
estimated at well under one request per second against a local API.

Doc 2 §30 says to baseline before optimising and not to invent numbers, so that
is Phase 6's question with real measurements in hand, not a class hierarchy built
on a guess. Recorded here so it is a deferral with a reason rather than an
oversight.

## Tests

`apps/desktop/workspace-windows.test.js` — the state machine: a new window is
assumed watched; `background` keeps working; only `obscured` stands down;
repeated states report no movement; events for closing or unknown windows are
ignored (Electron listeners outlive their windows and reuse ids); `watched()`
counts only readable windows; visibility never leaks into the restored layout.

`apps/web/src/desktop.test.ts` — the renderer half: absent outside the shell,
absent on an older shell with no such method, `background` reports focused,
`obscured` reports not, and the unsubscribe is handed back so a teardown does not
leave a listener on a window id about to be reused.
