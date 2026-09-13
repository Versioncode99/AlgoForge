# Performance engineering

Every number here was measured before the change was made and again after.
Nothing was optimised on intuition, and two of the four significant findings
were regressions this phase introduced and then caught by measuring.

Reproduce with:

```
uv run python scripts/campaign_comparison.py --cycles 320 --bars 30000
uv run pytest tests/api/test_research_factory.py --durations=5
```

---

## Method

`cProfile` over `run_backtest` on 40,000 synthetic 1-minute bars, and
`pytest --durations` over the campaign suite, which drives the real engine end
to end. The campaign suite is the better instrument for research throughput
because it exercises composition, rendering, the static guard, the smoke test,
the backtest and the judge together — the profile of a single backtest misses
everything that happens once per candidate rather than once per bar.

---

## Finding 1 — the data gate was a third of every backtest

**Measured, before.** `forge.data.validation.validate_bars` was **33% of a
40,000-bar backtest** and runs on every single one. At 200,000 bars: 1.383 s, of
which 1.103 s was a content hash over every bar's canonical JSON form.

Extrapolated to the 16-year 1-minute reservoir (~5.6M bars) that is roughly
**40 seconds per backtest**, plus a multi-gigabyte intermediate string — for an
answer about a dataset that cannot have changed since the last backtest ran over
it.

**What could not change.** The digest is data identity: it keys
`BacktestResult.make_id` and is written into split receipts and preregistrations.
A faster digest would be a *different* digest and every result recorded before it
would stop comparing. So the digest algorithm is untouched.

**What changed.** The six structural checks are computed over arrays instead of
in a Python loop, with findings ordered by the first bar that fails each one so
the receipt is byte-identical — the order is part of the receipt and the receipt
is stored. The expensive half is cached against a fingerprint of the bars
themselves, so it is computed once per distinct dataset rather than once per
backtest.

| 200,000 bars | Time | |
| --- | ---: | --- |
| Before | 1.383 s | |
| After, first call | 1.487 s | +7%, the fingerprint |
| After, repeat | **0.246 s** | **5.6x** |

The +7% on a cold call is the fingerprint, paid once; a campaign runs hundreds
of backtests over one dataset.

**How it is kept safe.** `tests/data/test_validation_identity.py` re-implements
the original bar-by-bar pass and asserts the two agree on a clean batch and on
every fault — including the order of several faults at once. A parametrised test
asserts that changing *any* field changes the fingerprint, because a fingerprint
that skipped a field would let one dataset answer from another's receipt.

---

## Finding 2 — a transformation made the exported strategy quadratic

**This was a regression introduced by this phase**, found by measuring rather
than by reasoning.

**Measured.** The campaign test suite went from 5.9 s to **44.5 s** per test.
The profile named it exactly:

```
7,096,320 calls  44.1 s cumulative   _range(w, shift, n)      in a generated strategy.py
13,298,729 calls 18.8 s cumulative   Window.session_start_index
```

Seven million calls to one session lookup in a single campaign. The cause: a
transformation reads its source once per offset its window spans — up to 240 —
and the **exported Python** re-evaluated the source from the bars every time.
The compiled definition had a history cache; its rendering did not.

**Fix.** The generated module keeps a source value per *bar it belongs to*, on
the same causality argument the compiled path uses: a source function bounded at
bar `at` is a function of `at` and the arrays, and its value does not change when
later bars arrive. The cache resets on an identity comparison against the base
array the window is a view of — and holds a reference to it, so the object cannot
be collected and its address reused while entries keyed against it survive.

| Campaign test | Before | After |
| --- | ---: | ---: |
| `test_a_bounded_campaign_does_research…` | 44.5 s | **8.2 s** |

Equivalence is not assumed: `verify_python` runs the compiled definition and the
generated module over the same bars and compares their ledgers bar for bar, and
`tests/strategy/test_primitives.py` does it for **every** observation and
**every** transformation in the catalogue.

---

## Finding 3 — the campaign built what it was about to throw away

**Measured.** 220 of 320 cycles in one campaign composed a definition, rendered
it to Python, ran the static guard, registered it in the template store — which
runs a smoke test over synthetic bars — and then asked the novelty gate whether
the claim was new. When the answer was no, the template was deleted again.

The verdict costs nothing and could have come first.

**Fix.** Assess, then build. The ordering comment that justified the old
sequence was about templates versus *hypotheses*, and is still satisfied.

| 320 cycles, expanded arm | Before | After |
| --- | ---: | ---: |
| Unique constructions | 29 | **51** |
| Seconds | 48.7 | 51.0 |

Same wall clock, 76% more research — because the cycles that were being spent on
work that got deleted now reach an experiment.

---

## Finding 4 — the sampler rebuilt its own candidate list every draw

**This was also a regression introduced by this phase.**
`grammar.candidates_for(shape)` assembles about a thousand constructions to
enumerate what a shape can carry, and the sampler called it once per attempt,
with up to 240 attempts per draw.

**Fix.** Memoised — the answer is a pure function of the vocabulary.

| | Before | After |
| --- | ---: | ---: |
| One draw | ~100 ms | **0.13 ms** |
| 300 draws | ~30 s | 0.039 s |

---

## Finding 5 — the IR evaluator, incidentally

The feature engine was rewritten to support transformations, and the
opportunity was taken to stop it rebuilding a frame per shift. A frame now
carries its right bound as an integer rather than slicing five arrays, and
resolves the session start lazily and only when a session feature asks for it.

| 40,000 bars | Before | After |
| --- | ---: | ---: |
| `london_breakout` | 0.424 s | 0.420 s |
| `vwap_reversion` | 1.234 s | 1.252 s |
| `trend_pullback` | 0.672 s | **0.589 s** (−12%) |

Trade counts are identical in all three (232 / 326 / 39), which is the point of
including them: the throughput change is not a behaviour change.

---

## Finding 6 — the test suite wrote the developer's own settings

Not a latency finding, but it was costing wall clock in the worst way: tests
were sharing state.

`create_app` resolves the settings file from the *repository*, not from the
isolated workspace, so every test that patched `/settings` wrote the real
`config/settings.json` — and every test after the first started from whatever
the last one had set. A test asserting "budget enforcement is on by default"
passed or failed depending on which tests ran before it.

The conftest already guarded the storage pointer against exactly this failure.
It now guards the settings file the same way: snapshot, clear so the test starts
from the shipped defaults, restore.

---

## Where the time goes now

Campaign cycle, 320 cycles over 30,000 bars, expanded arm: **0.159 s per cycle**,
against 0.065 s for the baseline arm. The difference is real work — an assembled
construction carries more features than a written archetype, and the expanded arm
reaches an experiment on far more of its cycles.

Backtest throughput on 40,000 bars: 32,000–95,000 bars/s depending on how many
features the definition declares and whether they are session-anchored.

---

## Remaining bottlenecks, unfixed and named

* **The canonical bar digest is still O(n) with a large constant** on a cold
  dataset: ~0.25 s per 200,000 bars for the fingerprint plus ~1.2 s for the
  digest itself the first time. At 5.6M bars that is roughly 7 s once, then 7 s
  per repeat for the fingerprint. Removing the repeat cost entirely needs an
  immutable dataset object that carries its own receipt, which is a change to
  how bars are passed rather than to how they are hashed.
* **Session-anchored features dominate the remaining per-bar cost.**
  `session_start_index` is a binary search over timestamps with a `datetime`
  comparison, resolved once per frame. Precomputing a session-start array per
  dataset would remove it; it is not done because the array would have to travel
  with the window, and the window's right bound is a structural guarantee that
  is not worth complicating for a single-digit percentage.
* **Backtests are single-threaded.** Concurrency was examined and not attempted:
  the generated strategy modules hold module-level state (`_LEVELS`, `_CONTEXT`,
  and now the transformation cache), so two concurrent backtests of the same
  template would share it. Making them safe means per-instance state in the
  exporter, which changes the module protocol every hand-written template also
  implements. Stated here rather than half-done.
* **The frontend bundle has three chunks over 500 kB** (charting libraries).
  Unchanged by this phase and unmeasured against a user-visible load time.
