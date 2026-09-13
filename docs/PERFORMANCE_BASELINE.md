# Multi-window performance baseline

Doc 2 §30 asks for measurable criteria and says twice not to invent numbers:
baseline first, then optimise the actual bottlenecks. This is the baseline, the
method that produced it, and what it does and does not license.

**Measured by** `apps/web/tests/electron/performance.spec.ts`, which launches the
real Electron shell against a real API. Raw figures land in
`docs/PERFORMANCE_BASELINE.json` on every run, so the next measurement has
something to be compared against rather than a paragraph to argue with.

**Run it:**

```
cd apps/web
xvfb-run -a npx playwright test --config=playwright.electron.config.ts
```

## What was observed

One container, `linux x64`, Node 22, Electron 44.2.0.

| Operation | Observed |
| --- | --- |
| Cold start to first window | **275 ms** |
| Shutdown | **143 ms** |
| Workspace window open (1st, 2nd, 3rd) | **210 / 30 / 45 ms** |
| Restore a two-window arrangement | **183 ms** |
| Resident memory, shell alone | **445 MB** |
| Resident growth per additional window | **+222 / +317 / +431 MB** cumulative |
| Resident delta after four open/close cycles | **+107 MB** |
| Renderer requests in 6 s — active / background / obscured | **10 / 4 / 0** |

## What the numbers say

**Window creation is sub-linear, not super-linear.** The first window costs 210 ms
and later ones 30–45 ms. The first pays for a cold renderer; the rest do not. Had
the third been slower than the second, that would have been the signature of an
O(n²) broadcast or a leak — a defect wherever it ran. It is not there.

**Memory grows roughly linearly, about 100–120 MB per window.** That is Chromium's
per-renderer cost rather than anything AlgoForge chose, and it is the reason Doc 2
§5 is right that not every workspace should become an OS window. The product
already defaults to `auto` placement for this reason.

**Four open/close cycles leave 107 MB outstanding.** Not nothing, and worth stating
plainly rather than rounding to zero. It is within what an allocator returns
lazily — renderer memory comes back when the OS gets to it, not on close — and it
did not grow per cycle, which is what a genuine leak looks like. Flagged for the
next measurement to compare against rather than declared clean.

**The visibility work does what it claimed.** Obscured reaches **zero** requests and
background stays at **4**. That is the whole design in one row: a window nobody can
see stops, and one that is simply not frontmost keeps working, because several
windows watched at once is the arrangement this product is for.

## A measurement that was wrong, and how

The first version of the visibility test minimised the window and counted
requests. It reported **6 of 11 requests still firing when hidden**, which reads as
a straightforward product defect.

It was an artefact. Under Xvfb there is no window manager, so `minimize()` does
nothing — `isMinimized()` returned `false`, the document never went hidden, and
the window polled on exactly as before. The test was measuring an unchanged
window and reporting it as a failure to stand down.

The test now drives the mechanism the shell actually uses: the main process sends
a visibility state, the renderer feeds it to the query layer's focus gate. That
is environment-independent and is the thing worth asserting. Recorded here
because a plausible number from a broken measurement is worse than no number, and
this one would have been believed.

## What these numbers are not

They are **not thresholds**, and the suite does not fail when they move. A figure
measured once on one container is a starting point; a test that goes red because
a laptop is busy teaches people to ignore it.

What *is* asserted is structural, because it holds on any machine:

- the third window must not cost dramatically more than the second;
- four open/close cycles must not leave hundreds of megabytes behind;
- an obscured renderer must do less work than an active one;
- a background renderer must still do some.

## Still not measured

Honest gaps, not oversights:

- **Panel drag and chart pan latency.** Both are pointer-driven, and measuring
  them meaningfully needs a frame-timing harness rather than a wall clock around
  an IPC call.
- **IPC volume.** No counter exists; adding one to the contract to measure it
  would change the thing being measured.
- **Database writes per operation.** Worth having, and better taken from the
  store's own instrumentation than inferred from the outside.
- **Docking and undocking latency.** The gesture landed after this harness; the
  operations themselves are pure functions over a grid and are unlikely to be
  where time goes, but that is a prediction, not a measurement.

## The optimisation this baseline does not license

`docs/DATA_FABRIC_ARCHITECTURE.md` defers cross-window deduplication of global
polls to this phase. The measurement does not justify it: an active renderer
issues roughly **10 requests per 6 seconds**, and against a local API three windows
is under one request per second in total. Brokering those through the main
process would add a real parallel data path — which Doc 2 §28 forbids without
cause — to save something that is not currently costing anything.

Revisit if a future measurement shows request volume growing with window count in
a way that matters. Deferred with a number attached rather than a feeling.
