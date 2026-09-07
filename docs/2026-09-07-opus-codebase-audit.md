# AlgoForge codebase audit — Opus 5

Branch: `main` @ `fdada02`
Date: 2026-09-07
Method: execution tracing and measurement. Every number here was produced on
this machine against the configured workspace, not estimated.

This audit follows two earlier ones (`2026-09-07-architecture-audit.md`,
`ui-overhaul-audit.md`) and does not repeat their findings. It covers what those
left open, plus one class of defect they did not look for.

Confidence tags: `[MEASURED]` = a number produced here; `[DOCUMENTED]` = read in
code and traced to its call sites; `[INFERRED]` = strongly implied, not executed.

---

## 1. Workspace under audit

| | |
|---|---|
| Workspace | `F:\Obsidian Vaults\AlgoForge-Vault` (vault mode) |
| Strategies | 399 |
| Backtest artifacts | 1,919 files, 3.19 GB |
| Legacy artifacts in the repo checkout | 1,316 files, 1.84 GB (pre-vault, orphaned) |
| Python | 3.13.12 |
| Tests at start | 484 |

The 1.84 GB in `F:\AlgoForge\data\backtests` is left over from before the vault
pointer was written. Nothing reads it. It is not deleted here because deleting
1,316 research artifacts is the operator's call, not mine.

---

## 2. The cold start, measured and fixed

The prior audit recorded "~52s" for the first `/strategies` call and left the
cause open. Measured here it was worse, and the cause was specific.

### Root cause `[MEASURED]`

`BacktestStore` kept its artifact projection **in process memory only**. Every
API start rebuilt it by fully parsing every artifact on disk.

| Operation | Cost |
|---|---|
| `os.scandir` over all 1,919 artifacts | **0.108s** |
| Reading all 3.19 GB from disk (warm cache) | ~2.6s at 1,264 MB/s |
| `json.loads` of the same bytes | **~40s at 80 MB/s** |
| Measured `GET /api/v1/strategies`, cold process | **78.5s** |
| Measured `GET /api/v1/strategies`, warm process | 0.001s |

So the entire cost was parsing 3.19 GB of JSON to recover about 200 KB of
scalars — a strategy id and a timestamp per file — and it was paid again on
every single launch.

Four more loops read a whole trade ledger per strategy to take two scalars:
`research_overview`, the assistant context, engine population pruning, and
strategy detail. The judge route read every artifact **twice**, once for the
runs and once more to count distinct parameter sets.

### Fix and result `[MEASURED]`

`apps/api/forge_api/artifact_index.py` persists the projection in SQLite beside
the artifacts. Artifacts are immutable and content-addressed, so each is parsed
exactly once for the life of the workspace rather than once per boot.

| | Before | After |
|---|---|---|
| `GET /api/v1/strategies`, warm process | 78.5s | **0.03s** |
| `import forge_api.main` | 2.50s | **1.02s** |
| Process ready | 2.75s | **1.26s** |
| Time to a populated Strategies screen | ~81s | **~1.4s** |

One-time backfill: 44s, in a background thread, once per workspace. Correctness
never depends on it — an unindexed artifact is parsed on demand.

Two import costs were also removed. `scipy.stats` (1.08s) was imported at module
scope by `forge.judge.statistics` for two scalar functions no gate calls until a
backtest has finished. `pandas` (0.42s) was imported by three modules, one of
which the API loads solely to call `load_keys`, a function that reads two text
files. Both are now imported on first use, with a test asserting the deferred
`norm` is scipy's own object rather than a substituted approximation.

### Data corruption found while measuring `[MEASURED]`

`backtest_3d7db535724e80e9f3e9e118.json` has its first 32,768 bytes replaced by
a session-lock JSON document followed by NUL padding, and is truncated mid-trade.
Another process wrote over a research artifact.

Nothing reported this. The file simply failed to parse and was skipped, so a
strategy silently lost a run. Worse, because the failure was not recorded, the
file re-entered the indexing queue on every read — 0.27s of pure retry across a
single strategy listing. Damaged files are now recorded with a taxonomy reason,
retried only if their size changes, and reported through `status()` and
`damaged()`.

---

## 3. The headline defect: four gates cannot fail

The prior audit found that G1 was a rubber stamp — `preregistered=True` was a
literal at every call site — and fixed it for the engine's own path. It did not
ask the same question of the other gates.

Asked systematically, of every gate: *can this gate fail, given the inputs the
application actually constructs?*

| Gate | Input | Supplied by | Can it fail? |
|---|---|---|---|
| G0 Data integrity | `data_gate_passed` | literal `True` in 5 of 7 call sites | **Only in the engine** |
| G1 Preregistration | `preregistered` | real in `engine.py`; literal `True` in `strategies.py` | **Not on the operator's path** |
| G2 Implementation | `implementation_tests_passed` | literal `True` at **every** call site | **No** |
| G3 Sample adequacy | trade count | measured | Yes |
| G4 OOS expectancy | net P&L | measured | Yes |
| G5 Multiple testing | DSR + trial spread | measured, adequacy-gated | Yes |
| G6 Robustness | profit factor, permutation p | measured | Yes |
| G7 Engine consistency | `engine_consistent` | **never supplied**; defaults `True` | **No** |
| G8 Risk | Calmar | measured | Yes |
| G9 Mechanism | `mechanism_aligned` | **never supplied**; defaults `True` | **No** |
| G10 Evidence tier | tier string | measured | Yes |
| G11 Selection integrity | PBO | measured, adequacy-gated | Yes |
| G12 Walk-forward | WF result | measured | Yes |
| G13 Path robustness | CPCV paths | measured | Yes |

### 3.1 G2 — the evidence exists on disk and is never produced `[DOCUMENTED]`

`StrategyLibrary.create_from_template` writes `test_strategy.py` for every
strategy, from `TEST_TEMPLATE`. That template's own docstring says:

> Every strategy ships a lookahead trap. A suite without one is rejected by the
> harness, because the cheapest way to fake an edge is to read the future.

There is no harness. `get_tests()` has exactly one caller — `strategy_detail`,
which serves the text to the interface for display. The suite is generated,
written, shown, and never executed. 399 strategies on this machine each carry an
unrun conformance suite, and G2 reports "tests pass" for all of them.

This is the same defect as G1 was, in a gate whose evidence is already sitting
on disk.

### 3.2 G7 — asserts a comparison that does not happen `[DOCUMENTED]`

`engine_consistent: bool = True` is a dataclass default that **no call site ever
overrides**. The gate's rule string reads "oracle tolerance passes". The module
that used to be called `oracles` was renamed to `capabilities` in a previous
pass precisely because it evaluates nothing — it reports whether
`nautilus_trader` is importable. So G7 stamps PASS on a tolerance comparison
against a reference engine that is never run.

The evolution report already states, in prose, "Nothing is calibrated. No number
has been reconciled against NinjaTrader's Strategy Analyzer." G7 says the
opposite, per run, in a machine-readable verdict.

### 3.3 G9 — same shape `[DOCUMENTED]`

`mechanism_aligned: bool = True`, never overridden. "mechanism not falsified" is
asserted without any falsification having been attempted.

### 3.4 Why this matters more than it looks

The judge's central virtue is that absent evidence yields `INCONCLUSIVE` and
never `PASS`. That rule is enforced rigorously for G5 and G11–G13, which are the
gates whose evidence arrives as an object that can be `None`.

It is not enforced for G0, G2, G7 and G9, whose evidence arrives as a `bool`
that defaults to, or is passed as, `True`. **A `bool` cannot represent absence.**
Four gates therefore convert missing evidence into a positive conclusion — the
one thing the design exists to prevent — and they do it invisibly, because a
gate that always passes looks exactly like a gate that was satisfied.

### 3.5 The consequence of fixing it, stated up front

Making these tri-state means `PASS` becomes unreachable until real evidence is
produced for each. That is the honest state of the system, but an inert ladder
is not an improvement either. So the fix order is **evidence first, strictness
second**:

1. Build a conformance runner so G2 has real evidence. (§3.1's suite already exists.)
2. Make G7 a real check that this installation *can* perform — bit-for-bit
   re-execution — and state plainly that venue calibration is a different claim
   it is not making.
3. Persist the data-quality receipt `run_backtest` already computes, so G0 reads
   a measurement rather than a literal.
4. Then flip G2/G7/G9 tri-state, so absence reads as INCONCLUSIVE.
5. G9 last, as a direction-matched entry-timing control.

---

## 4. What is genuinely strong and must not be touched

Confirmed by reading, not assumed:

- **`forge/judge/statistics.py`** — PSR, DSR, PBO-via-CSCV and the permutation
  test are correct implementations of the published estimators, with the
  per-period/annualised and excess/non-excess conventions enforced and
  documented. PSR returns 0.0 rather than claiming significance when its
  denominator goes non-positive.
- **`forge/research/validation.py`** — re-runs the parameter search inside every
  fold and every split. Rare, and correct.
- **Trial adequacy** — `MINIMUM_TRIAL_CONFIGURATIONS = 8`, with *present but
  inadequate* distinguished from *absent* in the observed value.
- **Burn-once holdout** — code-hash re-check before the seal opens.
- **`forge/memory/research.py`** — failure classes with declared reach, and
  `BOOKKEEPING`/`DATA`/`INFRASTRUCTURE` barred from pruning anything.
- **`forge/strategy/guard.py`** — static AST guard on every executed module.
- **`forge/provenance/snapshot.py`** — content-addressed run snapshots.
- **`apps/api/forge_api/actions.py`** — 17 bounded actions, no arbitrary-code verb.

---

## 5. Architecture, as built

```
apps/desktop        Electron shell, owns the API process
apps/web            React interface, lazy-loaded views
apps/api/forge_api  FastAPI routers, engine, orchestrator, action registry
packages/forge
  judge             gate ladder + statistics          <- the core
  research          splits, walk-forward, CPCV, validation runner
  strategy          templates, library, guard, runtime, backtester
  memory            research memory (pruning)
  provenance        run snapshots
  data              providers, archives, bar validation
  prop              prop-firm rule simulation (deliberately separate)
  agents            debate positions derived from verdicts
  ledger, vault, contracts, forgekeeper, analytics, capabilities
```

Boundaries are clean and the dependency direction is right: `forge` never
imports `forge_api`. The modular monolith is the correct shape here and should
not be split.

### 5.1 Absent by design, and honest about it

There is **no execution package, no connector, no risk engine, and no live-order
path anywhere in the repository**. Verified by search. For a paper-only research
instrument this is the safest possible state, and it should be preserved as an
explicit architectural boundary rather than an accident of not having got round
to it yet.

---

## 6. Risk register

| # | Risk | Location | Severity | State |
|---|---|---|---|---|
| 1 | G2 stamps PASS on a suite that is never run | `judge/engine.py`, all call sites | **High** | §3.1 |
| 2 | G7 asserts a reference-engine comparison that never happens | `judge/engine.py:138` | **High** | §3.2 |
| 3 | G9 asserts a falsification that never happens | `judge/engine.py:147` | **High** | §3.3 |
| 4 | G1 is real in the engine and a literal on the operator's own judge route | `strategies.py:971,1052` | **High** | §3 |
| 5 | G0 reads a literal rather than the receipt `run_backtest` already computes | `strategies.py`, `main.py` | Medium | §3 |
| 6 | Research artifact corrupted on disk by another process | workspace | Medium | §2, fixed |
| 7 | Mutating agent parity still absent | `actions.py` | Medium | carried over |
| 8 | 1.84 GB of orphaned artifacts in the repo checkout | `data/backtests` | Low | operator's call |
| 9 | `main.py` still judges a hardcoded P&L series for the seeded demo run | `main.py:137,156,201` | Low | labelled in meta |

---

## 7. Recommended order

P0, in dependency order — evidence producers before strictness:

1. Conformance runner; G2 gets real evidence.
2. Determinism check; G7 gets real evidence and an honest rule string.
3. Data-quality receipt persisted into the artifact; G0 reads it.
4. G2/G7/G9 tri-state — absence becomes INCONCLUSIVE.
5. G1 wired on the operator's judge route, as it already is in the engine.

P1:

6. Explicit research/execution boundary as code, so the absence in §5.1 is a
   guarantee rather than a gap.
7. Mutating agent parity.

Everything below that is P2 and is not worth trading against any of the above.
