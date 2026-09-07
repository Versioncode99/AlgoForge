# AlgoForge evolution — Opus 5 report

`3e7ae8f` → `4193780`, on `main`. Tests 484 → 628 Python, 12 → 12 Vitest, 15 Playwright.
Companion to `docs/2026-09-07-opus-codebase-audit.md`, which holds the audit and
the measurements.

Every number here was produced on this machine against the configured workspace
(`F:\Obsidian Vaults\AlgoForge-Vault`, 399 strategies, 1,919 backtest artifacts,
3.19 GB). Nothing is estimated.

---

## 1. What was inspected

The whole repository, by tracing execution rather than reading the README:
`packages/forge` (judge, research, strategy, memory, provenance, data, prop,
agents, ledger, vault, contracts, analytics, capabilities), `apps/api/forge_api`
(routers, engine, orchestrator, action registry, MCP facade), `apps/web`
(views, styles, tests), `apps/desktop`, config, rules, fixtures, scripts, and
the two prior audits.

Specifically and deliberately: every one of G0–G13, asking of each *can this
gate fail, given the inputs the application actually constructs?*

---

## 2. IMPLEMENTED

Things that did not exist before, each with a test that fails without them.

### 2.1 A durable artifact index — the cold start

**`apps/api/forge_api/artifact_index.py`**

The projection of the backtest directory lived only in process memory, so every
API start rebuilt it by fully parsing every artifact. It is now persisted in
SQLite beside the artifacts. Because artifacts are immutable and
content-addressed, each is parsed exactly once for the life of the workspace
rather than once per boot.

| | Before | After |
|---|---|---|
| `GET /api/v1/strategies`, warm process | **78.5s** | **0.03s** |
| `GET /api/v1/strategies`, live server over HTTP | — | 0.72s (823 KB payload) |
| `import forge_api.main` | 2.50s | **1.02s** |
| API process ready | 2.75s | **1.26s** |
| Electron cold start to window | — | 10.8s |
| First `/strategies` after an Electron cold start | ~81s | **0.91s** |

Correctness never depends on the backfill: an artifact the index has not seen is
parsed on demand. The one-time backfill is 44s, in a background thread, once per
workspace.

Four loops that read a whole trade ledger to take two scalars now read the
projection: strategy detail, the research overview, the assistant context, and
engine population pruning. The judge route read every artifact twice and now
reads them once. The prop matrix filtered candidates by reading every ledger
including the ones it was about to discard; it now pre-filters on the
projection (8.7s → 5.1s before the job is queued).

### 2.2 Deferred imports

`scipy.stats` (1.08s) was imported at module scope for two scalar functions no
gate calls until a backtest has finished. `pandas` (0.42s) was imported by three
modules, one of which the API loads solely to call `load_keys`, which reads two
text files. Both are now imported on first use.

A test asserts the deferred `norm` **is scipy's own object**. Substituting an
approximation to the normal quantile function would silently change PSR and DSR,
which is exactly the corruption those estimators exist to detect.
`tests/test_import_cost.py` enforces the budget from a subprocess.

### 2.3 A conformance harness — G2

**`packages/forge/strategy/conformance.py`**, `apps/api/forge_api/conformance_store.py`

Every strategy ships a `test_strategy.py` whose own docstring says *"a suite
without a lookahead trap is rejected by the harness"*. There was no harness.
`get_tests()` had one caller, which served the text to the interface for
display. 399 strategies each carried an unrun suite while G2 reported "tests
pass" for all of them.

The suite is now executed against the loaded module, passes the same static AST
guard as strategy code, and produces three-valued evidence. A suite that is
missing, refused by the guard, raises at import, or is green but has no
lookahead trap all read as INCONCLUSIVE. Detected lookahead fails G2 regardless.

Verified on the real workspace: **40 of 40 strategies pass their own suite** with
the trap present.

### 2.4 A determinism check — G7

**`packages/forge/strategy/determinism.py`**

Two claims were tangled under "Engine consistency". Venue calibration AlgoForge
cannot answer, and the rule string no longer implies it — a test asserts the
string says so. Engine determinism it can answer: re-executing the same strategy
over the same bars must produce the same trades, exactly. A backtest that does
not reproduce is not evidence about anything, and mutable module state, an
unseeded RNG or iteration over a set all break it.

Compared over a 6,000-bar window rather than the full series, because
non-determinism is a property of the code, not of data volume, and two extra
full-series runs per candidate would more than double the most expensive
operation in the system. The trade-off is recorded in `bars_checked`. A
divergence names the first differing trade and field.

### 2.5 An entry-timing control — G9

**`packages/forge/research/mechanism.py`**

`mechanism_aligned` was a default of `True` no call site overrode, so "mechanism
not falsified" was asserted without any falsification being attempted. Leaving it
permanently INCONCLUSIVE would have been honest and useless — nothing could ever
pass, the holdout would never open, and the promotion path would be dead code.

The null: *the same directions and holding periods, entered at uniformly random
times, would have done as well*. The control resamples entry timing and nothing
else, with the same point value and round-trip cost. Direction and holding
period are held fixed so it is not re-testing what G4 and G6 cover, and so a
long book in a rising market is compared against random long entries in that
same rising market. It is distinct from G6's permutation test, which shuffles
the signs of realised returns: that asks whether the P&L could be noise, this
asks whether the entry times could have been chosen by a coin.

**A real bug was found by a test that was wrong first.** On a series with little
dispersion every control draw earns almost exactly what the strategy earned, and
the two differ only in the last bits of the float. A strict `>=` counted zero of
400 draws as matching and reported p = 0.0025 — declaring rounding error to be
timing skill. Ties now count against significance with a tolerance scaled to the
magnitudes involved, so a point-mass null reads p = 1.0. There is a regression
test.

### 2.6 The data-quality receipt — G0

`run_backtest` has always called `validate_bars` and refused to run on bars that
fail it, so the check was real; the receipt was discarded and G0 handed a
literal, with a rule reading "G0 receipt passes" for a receipt nobody kept.
`BacktestResult` now carries `data_quality` and the gate reads it. The engine's
own site used `data_gate_passed=real_data`, an inference about the code path
rather than a measurement of the data; it reads the receipt too.

### 2.7 Preregistration on the operator's path — G1

**`apps/api/forge_api/preregistration_store.py`**

The engine froze a claim and re-derived it; the judge route a person drives
passed `True`. The same gate was a measurement in one path and a rubber stamp in
the other, and the verdict does not record which path produced it.

The claim is now frozen when a real-data backtest starts and re-derived when the
judge asks. Records are append-only: overwriting one would let a second freeze
launder a moved claim into a held one. "Never preregistered" and "preregistered
then moved" are different findings with different observed values. The hash does
not depend on the wall clock, or every candidate would fail G1 for the crime of
time having passed.

### 2.8 The research/execution boundary

**`packages/forge/execution/`**, **`packages/forge/risk/`**, `tests/execution/test_boundary.py`

AlgoForge has never had a live-order path — because nobody wrote one, which is a
different kind of true from "the repository refuses to contain one".

Nine lifecycle stages with enumerated transitions. No research stage has an edge
into an execution stage; `VALIDATED` cannot jump to `PAPER`; `PAPER` → `DEPLOYED`
is a second authorization, because approving research is not approving capital.
An `Authorization` has no optional fields — no default author, no default risk
profile — so an agent cannot manufacture one by omitting arguments.
`LIVE_EXECUTION_AVAILABLE` is `False` and the refusal says *no connector exists*
rather than complaining about a missing signature.

`forge.risk` is provider-neutral, consulted by the lifecycle rather than by
execution code (the only safe ordering while the execution half does not exist).
"Unlimited" is not expressible: a non-positive ceiling is refused at
construction. `enabled=False` is a kill switch that names itself in the refusal.
Breaches accumulate rather than short-circuit.

The boundary test parses every module under `packages/` and `apps/api/` and
asserts no broker client library is imported, nothing defines a
submit/place/cancel/modify order function, and no research package imports
execution, risk or connectors. It is meant to be noisy to break.

### 2.9 Corruption reporting

`backtest_3d7db535724e80e9f3e9e118.json` has its first 32 KB overwritten by a
session-lock document and NUL padding — another process wrote over a research
artifact. Nothing reported it; the file failed to parse and was skipped, so a
strategy silently lost a run, and because the failure was not recorded the file
re-entered the indexing queue on **every read** (0.27s of pure retry per
strategy listing). Damaged files are now recorded with a taxonomy reason,
retried only if they change, and surfaced through `status()` and `damaged()`.

---

## 3. PARTIALLY IMPLEMENTED

- **The error taxonomy** exists where the index and the conformance harness use
  it (`DATA_CORRUPTED`, `SCHEMA_ERROR`, `DATA_UNREADABLE`, `GUARD_REFUSED`,
  `NO_LOOKAHEAD_TRAP`, `NOT_CHECKED`). It is not yet applied across every API
  error path.
- **Data Health** gains a real corruption signal (`damaged()`), but the
  coverage/gap/timestamp-integrity matrix from the brief is still not built.
- **Risk profiles** are modelled, validated and tested, but not yet persisted or
  exposed through the API — nothing can currently create one from the interface.

---

## 4. SCAFFOLDED — clearly labelled

- **`forge.execution` contains no connector, no order type, no position
  tracking.** This is deliberate, documented in the module docstring, and
  enforced by a test. Building an order-submission path before there is a venue
  would be speculative infrastructure, and an unused one is worse to have than
  none.
- **`Stage.DEPLOYED` is modelled but unreachable.** It exists so the state
  machine can refuse entry to it for the true reason.

---

## 5. NOT IMPLEMENTED

Stated plainly, because a report that lists only wins is not useful.

- **Charting (§11).** No chart exists. This is the largest single gap against
  the brief and it was traded, deliberately, for the scientific correctness
  work, which the brief itself ranks P0 above P2.
- **Strategy IR and export (§16, §17).** Not started.
- **Connectors (§15), DOM, watchlists, workspaces, multi-chart (§12–14).** Not
  started. The boundary they would sit behind now exists.
- **Search-space visualisation and the lineage DAG (§20, §21).** Still lists,
  not graphs.
- **Mutating agent parity (§26).** Carried over from the previous report and
  still open.
- **Calibration.** Nothing has been reconciled against NinjaTrader's Strategy
  Analyzer. Every P&L in this system remains modelled.

---

## 6. The consequence you should know about

**All 1,919 artifacts already on this workspace now read INCONCLUSIVE at G0 and
G1**, because those runs genuinely have no recorded data receipt and no frozen
claim. They are not promotable until re-run.

That is the correct outcome — claiming otherwise was the defect — but it is a
real change to what the application will tell you tomorrow morning, and it is
not a bug report when you see it.

Verified that the new path works: a fresh backtest on `nq_1m_16y` produces a
real receipt (accepted, 0 findings, 3,964 rows, content-hashed) and a frozen
claim that re-derives to the same hash.

### The ladder is not inert

Five real candidates through the operator's judge route, after all the changes:

```
ou_half_life_reversion_451a919  G0? G1? G2. G3. G4. G5X G6X G7. G8X G9X G10. G11? G12? G13?
ou_half_life_reversion_a90c5a5  G0? G1? G2. G3. G4. G5X G6X G7. G8X G9X G10. G11. G12. G13.
ou_half_life_reversion_287c7c3  G0? G1? G2. G3. G4. G5X G6X G7. G8. G9. G10. G11X G12X G13X
ou_half_life_reversion_b1fd392  G0? G1? G2. G3. G4. G5. G6. G7. G8. G9. G10. G11X G12X G13X
ou_half_life_reversion_a389223  G0? G1? G2. G3. G4. G5X G6X G7. G8X G9X G10. G11X G12X G13X
```

G2 passes 5/5 and G7 passes 5/5 on real evidence. **G9 passes 2 and fails 3** —
three candidates whose entry timing does not beat random placement, which
nothing in the system could previously detect. The gates discriminate; they are
neither rubber stamps nor a blanket rejection.

---

## 7. Testing

| Suite | Result |
|---|---|
| Python (`pytest`) | **628 passed** (was 484) |
| `ruff check` | clean |
| `mypy --strict` | clean, 96 source files |
| Vitest | 12 passed |
| Playwright | **15 passed** against the live app |
| Widths 1280 / 1440 / 1920 / 2560 | no horizontal overflow, no console errors |
| Electron, reusing a running API | window titled `AlgoForge`, clean shutdown |
| Electron, spawning its own API | API up 4.8s, window 10.8s, clean shutdown, no orphan |

No test was deleted or weakened to make the suite green. One Playwright test was
genuinely failing and was **right to**: it waited 120s for a prop matrix that,
at this workspace's 267 qualifying strategies, is 1,068 simulations at ~2.1/s —
about eight and a half minutes. Waiting for completion asserts that the machine
is fast, not that the product is correct. The test's own comment says the thing
that must never happen is silence; it now accepts the third non-silent outcome
(a job still working) and checks the job bar carries a real count against a real
total. Both original assertions are unchanged.

---

## 8. What was already good and was not touched

- `forge/judge/statistics.py` — PSR, DSR, PBO-via-CSCV and the permutation test
  are correct implementations of the published estimators, conventions enforced
  and documented. Not altered; only its import was deferred, and a test proves
  the values are unchanged.
- `forge/research/validation.py` — re-runs the parameter search inside every
  fold and split. Rare, and right.
- Trial adequacy, the burn-once holdout, research memory's failure classes with
  declared reach, run snapshots, the static strategy guard, the bounded action
  registry. All left alone.

---

## 9. Remaining limitations

1. Nothing is calibrated. Every P&L is modelled.
2. Snapshots are auditable, not hermetic: no Python version, no package
   versions, no copy of the market data.
3. The validation grid is still fixed at nine configurations — clears the
   adequacy floor, thin for PBO.
4. `main.py` still judges a hardcoded sample P&L series for the seeded demo run,
   labelled in the response meta.
5. 1.84 GB of orphaned pre-vault artifacts sit in `data/backtests` in the
   checkout. Nothing reads them. Deleting 1,316 research artifacts is the
   operator's call, not mine.
6. `Strategies-*.js` is a 611 kB chunk. Measured, not yet split.
7. The conformance harness has no timeout. A suite that loops forever hangs the
   caller — but so does a strategy whose `entry_signal` does, and that already
   executes on every backtest. No new class of risk; stated rather than
   discovered.

---

## 10. Recommended next priorities

1. **Re-run the library** so G0 and G1 have evidence, or accept that the existing
   1,919 artifacts are historical and start the catalogue fresh.
2. **Charting.** The largest gap against the brief, and the thing that would most
   change what the product feels like. Trades, entries, exits and equity over
   real bars, driven by the artifacts that already exist.
3. **Persist and expose risk profiles**, so the lifecycle's authorization step is
   reachable from the interface rather than only from Python.
4. **Mutating agent parity**, still requiring the validation entry point to be
   extracted from its router closure.
5. **Calibrate against NinjaTrader's Strategy Analyzer.** Everything above is
   internal consistency. This is the only step that makes any number real.

---

## 11. On production readiness

Not production ready, and this work does not make it so. It is a research
instrument that is now measurably faster and materially more honest: five gates
that could not fail now can, one of them by a test that finds something nothing
in the system could previously see, and the absence of a live-order path is a
checked invariant rather than an accident.
