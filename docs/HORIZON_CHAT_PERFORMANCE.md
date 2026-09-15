# Chat performance — what was measured, and what changed

Reproduce with:

```
.venv/bin/python scripts/measure_chat.py --strategies 400  --samples 25
.venv/bin/python scripts/measure_chat.py --strategies 2000 --samples 15
```

Raw output: `docs/HORIZON_CHAT_PERF.json`, `docs/HORIZON_CHAT_PERF_2000.json`.

## Method, and what it deliberately excludes

**No provider is called.** A single "chat latency" number is dominated by the
model and tells nobody what to fix: the provider's time is not this
application's to improve, and including it would swamp everything that is. So
what is measured is the local work done *before* a provider is reached, plus the
conversation store's own reads and writes.

**Before and after are measured on the same machine in the same process.** The
"before" figure is this build with the context cache defeated
(`assistant._context_cache = None` between samples), not a number recorded on
other hardware at another time. Everything else in the path is identical.

Machine: the session container, Linux x86-64, Python 3.13.12. Each figure is
p50/p95 over the stated sample on a library of the stated size.

## The result

### Building the model's context, per message

`Assistant.context()` called `library.list_specs()`, which globs every
`*/spec.json` and validates each one through Pydantic. It ran **once per
message**, so the cost scaled with the library and was paid before the question
had been looked at. It is now cached against a fingerprint of the library — the
count of spec files and the newest mtime, one `stat` per directory — and
rebuilt only when the library moves.

| Library | Before p50 | Before p95 | After p50 | After p95 | Change |
| ---: | ---: | ---: | ---: | ---: | --- |
| 400 strategies | 15.1 ms | 15.9 ms | 3.5 ms | 3.6 ms | **4.3× faster** |
| 2000 strategies | 95.2 ms | 136.1 ms | 21.5 ms | 29.0 ms | **4.4× faster** |

The cold (first-message-after-a-write) path is unchanged by design: 16.4 ms at
400 strategies, because the work still has to happen once.

The remaining 21 ms at 2000 strategies is the *uncached* half of the context —
the backtest count and the last twenty-five activity rows — plus building the
row list from the cached specs. It is bounded by `CONTEXT_STRATEGIES = 60`,
which is also why the prompt stopped growing with the library.

### The conversation store

Budget: p95 under 50 ms locally. Measured at 400 strategies, 25 samples:

| Operation | p50 | p95 |
| --- | ---: | ---: |
| Create a conversation | 1.01 ms | 1.20 ms |
| Append a turn | 1.20 ms | 1.28 ms |
| Read a thread | 0.37 ms | 0.45 ms |
| List conversations | 0.77 ms | 0.81 ms |

### Accepting a run

Budget: new-conversation acknowledgement p95 under 200 ms; server acceptance
visible p95 under 300 ms.

| Measure | p50 | p95 |
| --- | ---: | ---: |
| `ChatRunner.start` (400 strategies) | 1.69 ms | 9.93 ms |
| `ChatRunner.start` (2000 strategies) | 2.30 ms | 20.04 ms |

`tests/api/test_chat_runs.py::test_a_run_is_acknowledged_before_the_model_is_called`
asserts the same property against a deliberately slow chat service: the run
returns in under 200 ms while the "model" is still running.

### A whole local turn, provider excluded

| Library | p50 | p95 |
| ---: | ---: | ---: |
| 400 strategies | 7.9 ms | 8.9 ms |
| 2000 strategies | 25.9 ms | 26.8 ms |

Budget: pre-model local context preparation p95 under 350 ms. Met with two
orders of magnitude to spare, which is the useful finding — **the local overhead
was never the reason chat felt slow.** What made it feel slow is in the next
section, and it is a shape problem rather than a speed one.

## What actually made it feel slow

**The question did not appear until the answer did.** `POST
/conversations/{id}/messages` wrote the turn, called the model, waited, and
returned both. A nine-second model looked like a Send button that had stopped
working, and no amount of the millisecond work above changes that. The fix is
the run protocol, not an optimisation: the question is on screen before the
network is touched, and the answer arrives through a stream that can be watched
and stopped.

**Opening Chat loaded the whole workstation.** `App.tsx` fetched health,
summary, *every strategy*, the activity feed, the inbox, the appearance and the
active workspace on every route — for a screen that shows none of them. Two
queries remain in the shell (health, which decides whether anything renders, and
summary, which is the header's only number) and the rest moved to the screens
that display them.

Asserted rather than described, in `apps/web/src/App.test.tsx`:

* `opening Chat does not fetch the strategy library`
* `opening Chat does not fetch the activity feed`
* `the composer is usable before anything but the shell has loaded`
* `the strategy library is still fetched by the screen that shows it` — the
  point being route-local, not removed.

**Chat pulled in every chart renderer.** `ArtifactVisual` statically imported
`AnalysisChart`, `RegimeMatrix` and the shared chart module, which between them
are ECharts and its canvas renderer — about 1.2 MB of JavaScript in the module
graph of a conversation that never mentions a chart. Each is behind `lazy` now.

Verified against the built bundle rather than the source:

```
$ grep -oE 'from"\./[A-Za-z]+-[A-Za-z0-9_-]+\.js"' dist/assets/chat-*.js | sort -u
from"./index-BjwHRSBV.js"

$ grep -oE '"\./(AnalysisChart|RegimeMatrix)-[A-Za-z0-9_-]+\.js"' dist/assets/chat-*.js
"./AnalysisChart-D0QkEGuX.js"
"./RegimeMatrix-Ut08h7os.js"
```

The chunk holding `ArtifactVisual` **statically** imports only the shared
bundle; the two renderers appear as dynamic imports, fetched when somebody
presses "Show the chart".

| Chunk | Size | Fetched when |
| --- | ---: | --- |
| `Chat-*.js` | 16.2 kB | Chat opens |
| `chat-*.js` (shared conversation code) | 6.4 kB | Chat opens |
| `AnalysisChart-*.js` | 578.6 kB | a chart artifact is expanded |
| `installCanvasRenderer-*.js` | 585.6 kB | a chart artifact is expanded |

## Budgets: met, and the two that are not measured here

| Budget | Status | Evidence |
| --- | --- | --- |
| New conversation acknowledgement p95 < 200 ms | **met** | 1.20 ms |
| Server acceptance p95 < 300 ms | **met** | 9.93 ms at 400, 20.04 ms at 2000 |
| Pre-model local context prep p95 < 350 ms | **met** | 3.6 ms at 400, 29.0 ms at 2000 |
| Conversation DB operations p95 < 50 ms | **met** | 1.28 ms worst |
| No full-strategy-list request to open Chat | **met** | asserted in `App.test.tsx` |
| User message visible after Send < 100 ms | **met by construction** | rendered optimistically before the request; `chat.test.tsx::the question appears before the answer does` |
| Stop acknowledged within 500 ms locally | **met by construction** | the composer switches to "Stopping…" synchronously; `chat.test.tsx::Send becomes Stop while a run is in flight` |
| Warm chat navigation to usable composer p95 < 1 s | **NOT MEASURED** | needs a browser against a running API; the shell's fetches are asserted instead |
| Cold chat navigation p95 < 2 s | **NOT MEASURED** | same |
| Time to first visible response, decomposed | **PARTIAL** | the local half is above; provider TTFT is not measured because no provider credential is configured in this environment |

The three unmeasured rows are marked so rather than estimated. A number nobody
measured, printed beside numbers somebody did, is worse than a gap.
