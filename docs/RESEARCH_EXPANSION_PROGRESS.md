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
| IR feature kinds | 21 | **47** |
| — observations | 21 | 35 |
| — transformations | 0 | 12 |

*(The commit message for this phase says 48. The measured figure is 47 — 35
observations and 12 transformations. `scripts/measure_vocabulary.py` prints it.)*

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

---

## Phase 2 — mechanism vocabulary (complete)

New module `packages/forge/research/mechanisms.py`: **12 mechanisms**, each
carrying a claim, a falsifiable prediction written against the observation the
construction actually made, the feature categories a signal must read to be
testing it, what a failure rules out and what it leaves open, a **stance**
(continuation or reversion) and a **reading** (which end of its own distribution
the claim is about).

The last two are not labels. Stance decides whether a trailing exit or a fixed
target is coherent with the claim — a fixed target caps exactly what a
continuation claim predicts, and then the result measures the exit. Reading is
what stops "quiet stretches cluster" being attached to a signal that only fires
when volatility is loud: the prediction would ask for a comparison across a
split the sample never straddles.

## Phase 3 — the composition grammar (complete)

New module `packages/forge/research/grammar.py`. A construction is assembled
rather than selected:

    OBSERVABLE -> TRANSFORMATION -> SHAPE -> STANCE -> REGIME GATE -> SIGNAL

| Measured | Before | After |
| --- | --- | --- |
| Distinct entry signatures | 10 | — |
| Distinct triggers | — | **1,113** |
| Gate candidates | 0 | 183 |
| Reachable structural signatures | 10 | **408,471** |
| Observables | — | 31 |
| Shapes | — | 5 |

Size alone would be worthless, so most of the module is refusals:

* **Units must agree.** A shape states the unit each slot needs. A scale-
  dependent reading cannot meet a scalar threshold, so "ATR above 3" is not
  expressible and "ATR in the top decile of its own last two hundred" is. A
  standard deviation of price is measured in price and is still not a price that
  can be crossed, so it carries its own unit.
* **A trigger must be directional.** "Long when the ATR percentile is high,
  short when it is low" has a signal on every bar and makes no claim about
  direction. Magnitude readings can gate a signal; they cannot be one. A
  threshold's short leg is stated against the unit's *neutral point* rather than
  by flipping the long leg's comparison, because the flip gives a short that is
  true almost always.
* **A signal must be able to see its own mechanism**, in both category and
  reading.

### Not a parallel pipeline

`synthesis.archetype_from_spec` converts an assembled construction into a real
`Archetype`, so `compose`, the template store, the static guard, the smoke test,
the novelty gate, the frontier and the campaign record all run the code they
already ran. There is no second route for an assembled strategy to take and
therefore no second set of protections to keep in step.

### Wired into the director

`ResearchDirector._pick_archetype` and `_archetype_for_item` now draw from both
halves. The ten written archetypes are the curated seeds and are tried first;
past them, `ASSEMBLED_SHARE = 0.75` of draws are assembled. Signatures already
built are excluded, recovered from two places: the in-memory attribution record,
and **the generated template's own key**, which carries the signature prefix and
survives a restart. Without the second, a resumed campaign re-proposes its own
earlier work and the gate refuses it — activity with no progress.

### Defects found by the integration

5. **Assembled constructions were given family names the registry refuses.**
   `_family_for` returned `trend_following` and `price_action`; the registry
   ships `breakout, carry, cross_asset, event_driven, liquidity, mean_reversion,
   microstructure, momentum, seasonality, session_structure, statistical,
   volatility`. The template write failed and the cycle recorded an ERROR for a
   construction that was fine. Status: **fixed**, with every value in the
   mapping now a shipped family key.
6. **A campaign lookup returned a context manager.** `_serving` is a
   `contextmanager`; using it as a getter produced
   `AttributeError: '_GeneratorContextManager' object has no attribute 'serves'`
   and killed a cycle. Status: **fixed** (`campaigns.get`).
7. **Draws with unusable warm-ups burned cycles.** Warmup is derived from each
   parameter's declared *maximum*, so a construction sweeping a long window
   needed 1,400+ bars before computing anything and came back `BLOCKED` — true,
   and a cycle spent learning nothing. Status: **fixed**: parameter ranges
   narrowed, and `MAX_ASSEMBLED_WARMUP = 800` enforced at draw time where it
   costs one composition instead of a backtest. Measured warm-up p50 fell from
   397 to 367 bars and p100 from 4,007 to 1,657, with over-budget draws refused
   rather than run.
8. **Gate levels could not fire.** A "ratio" gate level defaulting to 1.5
   applied to a quantity centred on zero (an acceleration of a bounded ratio)
   fires essentially never. `ratio` is no longer a gate unit at all: a gate is
   compared against a number, so only units with a known range qualify, and a
   ratio observable declares its own. Status: **fixed**; gate candidates fell
   from 227 proposed to 183 usable, checked once at import rather than once per
   wasted draw.

### Measured, 200 draws over 9,000 bars

`uv run python scripts/measure_vocabulary.py --cycles 200`

| | |
| --- | --- |
| Unique structural signatures | 200 of 200 |
| Collisions | **0** |
| Composed and compiled | 200 / 200 |
| Static-guard failures | 0 |
| Backtested (rest over warm-up budget) | 191 |
| Took trades | 138 |
| Silent (no trades on 9k bars) | 53 |
| Distinct mechanisms exercised | 9 of 12 |
| Distinct feature kinds exercised | 38 of 47 |
| Distinct families | 7 |
| Wall clock | 30.1 s |

## Phase 4 — the measured backtest bottleneck (complete)

`forge.data.validation.validate_bars` was 33% of a backtest and ran on every
one. Two changes, both verified byte-identical against a re-implementation of
the original bar-by-bar pass (`tests/data/test_validation_identity.py`):

* the six structural checks are computed over arrays, with the findings ordered
  by the first bar that fails each one so the receipt is unchanged — the order
  is part of the receipt and the receipt is stored;
* the expensive half, a content hash over every bar's canonical form, is cached
  against a **fingerprint of the bars themselves**. The digest is data identity:
  it keys `BacktestResult.make_id` and is written into split receipts and
  preregistrations, so a faster digest would be a different digest and every
  result recorded before it would stop comparing. It is therefore computed once
  per distinct dataset rather than once per backtest.

The fingerprint covers every field that reaches the canonical form, and a
parametrised test asserts that changing any one of them changes it — a
fingerprint that skipped a field would let one dataset answer from another's
receipt.

| 200,000 bars | Time |
| --- | --- |
| Before | 1.383 s |
| After, first call | 1.487 s (+7%) |
| After, repeat | **0.246 s (5.6x)** |

A campaign runs hundreds of backtests over one dataset, so the trade is
strongly positive; the 7% is the fingerprint, paid once.

---

## Open work

External research defaults and pipeline, agent roles and per-role model
configuration, the budget enforcement switch, settings and frontier UX, the
bounded campaign comparison, adversarial testing, security regression, and the
reports.
