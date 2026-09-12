# Research expansion — progress log

Working branch: `claude/algoforge-research-expansion-s1sznx`.
Baseline verified on `main` before any change: **2,719 backend tests passing**,
ruff clean, `mypy --strict` clean.

This file is the running record: what was measured, what was changed, what it
cost, and what is still open. It is written as work happens rather than
assembled afterwards, so a session that is interrupted can be resumed from it.

---

## Phase 0 — audit and measurement (complete)

Nothing was changed until the bottleneck was measured rather than assumed.

### The construction space, measured

`scripts` run against `main`:

| Quantity | Measured |
| --- | --- |
| Signal archetypes (`forge.research.synthesis`) | 10 |
| IR feature kinds (`forge.strategy.ir.FEATURE_ARITY`) | 21 |
| Python templates (`forge.strategy.templates`) | 12 |
| Enumerable definition hashes (archetype x direction x session x exit) | 600 |
| **Distinct entry signatures** | **10** |
| **Distinct structural signatures** | **10** |

The ceiling is the last two rows, and the cause is exact:
`forge.research.synthesis.compose()` takes an `Archetype` whose
`long_condition` and `short_condition` are **constants**. Everything the
composer varies — direction, session gate, exit style — leaves the entry
condition untouched. Ten archetypes therefore means ten entry signatures, for
ever, and the novelty gate's `STRUCTURAL_FEATURE_CHANGE` check (at least 34% of
the feature set must change) can only ever be satisfied by jumping between
archetypes. That is why 78 of 81 refusals were `SAME_CONSTRUCTION`: the gate was
right, and there was nothing else to propose.

**The novelty gate was not weakened.** The vocabulary underneath it was the
problem, and that is what this phase changes.

### Performance, measured

Profiled with `cProfile` over `run_backtest`, 40,000 synthetic 1-minute bars:

| Blueprint | Time | Throughput |
| --- | --- | --- |
| `london_breakout` | 0.424 s | 94,341 bars/s |
| `vwap_reversion` | 1.234 s | 32,411 bars/s |
| `trend_pullback` | 0.672 s | 59,528 bars/s |

The profile named a bottleneck that is not in the strategy at all:
`forge.data.validation.validate_bars` is **33% of a backtest** and is called on
every single run. It serialises every `Bar` through pydantic and builds one
canonical JSON string over the whole dataset before hashing it. Measured at
200,000 bars: 1.418 s total, of which 1.103 s is the digest. Extrapolated to the
16-year 1-minute reservoir (~5.6M bars) that is ~40 s **per backtest**, plus a
multi-gigabyte intermediate string. A campaign running hundreds of backtests over
one dataset pays it hundreds of times for an answer that cannot have changed.

A numpy-buffer fingerprint over the same bars costs 0.122 s — **11.6x cheaper**
than the full validation it can key a cache on.

### Defects found during the audit

1. **Dead settings control.** `apps/web/src/views/Settings.tsx` renders an
   editable "OpenAI-compatible base URL" field. `control.py` accepts the field
   and explicitly discards it (`proposed_url = base_url_for(provider)`), and
   `settings_store.load()` derives the URL from the provider id on every read.
   The operator can type a URL, see the save succeed, and have it silently
   revert. Status: **open**.
2. **Misleading provider warning.** The same view still explains that
   "OmniRoute is not listening", for a provider `providers.py` says was removed
   and has not come back. Status: **open**.
3. **Role vocabularies that do not meet.** `settings_store.ROLES` has 9
   model-routing roles; `forge.research.agents.AgentRole` has 10 research roles.
   Neither maps to the other, so choosing a model "per role" does not choose the
   model any research agent uses. Status: **open**.
4. **Budget cannot be turned off.** `BudgetSettings` has no enabled flag, so the
   ceilings are unconditional. Status: **open**.

---

## Phase 1 — feature primitives and transformations (complete)

New module `packages/forge/strategy/primitives.py`: the closed catalogue, split
into **observations** (computed from the bars) and **transformations** (computed
from another declared feature's own history).

| | Before | After |
| --- | --- | --- |
| IR feature kinds | 21 | **48** |
| — observations | 21 | 36 |
| — transformations | 0 | 12 |

New observations: `typical_price`, `bar_range`, `true_range`, `gap`, `clv`,
`signed_volume`, `upside_vol`, `downside_vol`, `efficiency_ratio`,
`variance_ratio`, `return_autocorr`, `session_high`, `session_low`,
`session_range_position`.

New transformations: `mean`, `stdev`, `zscore`, `percentile_rank`, `slope`,
`accel`, `change`, `pct_change`, `ewm`, `max_of`, `min_of`, `persistence`.

`Feature` gained a `source` field naming the feature a transformation reads.
Chains are allowed to depth 3 (`slope` of `percentile_rank` of `atr` is a real
construction), and the validator refuses: a transformation with no source, an
observation carrying one, a source that is not declared, a chain that loops, a
chain deeper than the bound, and a source whose own window length is itself a
feature (which could not be retained).

### How this stayed causal

A transformation reads its source's history. Recomputing that history from the
bars on every bar is quadratic, so each source is computed once per bar and
kept (`_History` in `ir.py`). Two properties make that sound rather than merely
fast, and both are asserted by tests:

* A stored value is computed from a frame bounded at the bar it belongs to, so
  it is the same number whether computed when that bar closed or during a
  backfill later. If it were not, caching would be lookahead.
* The store resets when the parameters change or when the bars underneath move.

`required_warmup()` now walks the whole source chain and sums the windows rather
than taking the largest, and a test asserts the invariant that makes the cached
evaluator and a fresh recomputation agree:
`required_warmup() >= max(source_history().values()) + 1`.

### Semantic correction found on the way

Session-anchored features read at a shift (`Cross` reads its operands one bar
back) were anchored to the *current* bar's session rather than the shifted
bar's, in both the IR and the export. Both now anchor to the bar being read, and
`Window.session_start_index` takes an optional index and refuses one past the
last closed bar. The three shipped blueprints produce **identical trade counts**
before and after (232 / 326 / 39), so nothing measured changed.

### Export

`to_python` now emits one function per declared feature rather than an inlined
expression, which is what makes a transformation expressible at all: it needs
something it can call at each offset its window spans. `verify_python` proves
the generated module reproduces the compiled definition bar for bar, and tests
cover **every observation and every transformation in the catalogue**.

### Performance effect

| Blueprint | Before | After |
| --- | --- | --- |
| `london_breakout` | 0.424 s | 0.420 s |
| `vwap_reversion` | 1.234 s | 1.252 s |
| `trend_pullback` | 0.672 s | 0.589 s (-12%) |

A frame no longer slices five arrays; it carries the bound as an integer, and
the session start is resolved lazily and only when a session feature asks.

### Verification

* 2,751 backend tests passing (2,719 baseline + 32 new).
* `ruff check` clean, `mypy --strict` clean across 190 source files.
* New suite: `tests/strategy/test_primitives.py`.

---

## Open work

Phases 2 onward: the composition grammar, mechanism vocabulary, external
research defaults, agent roles and per-role model configuration, budget
enforcement switch, the `validate_bars` fix, campaign measurement, and the
reports.
