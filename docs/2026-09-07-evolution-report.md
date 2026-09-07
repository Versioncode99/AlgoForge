# AlgoForge evolution — report

Branch `feat/industry-standard-validation`, `da92bf6` → `55a729e`, 2026-09-07.
Tests 258 → 416.

Companion to `docs/2026-09-07-architecture-audit.md`, which holds the Phase A
audit and the running status log. This is the honest account of what changed,
what did not, and how the result now compares with the three reference systems.

---

## Implemented

Things that did not exist before and now work, each with a test that would fail
if they stopped working.

### Research memory that prunes — `packages/forge/memory/research.py`

Durable SQLite failure record. Each failure carries a **class**, and each class
declares how far its evidence reaches in normalised parameter space. The engine
consults it before `Experiments.reserve`, so a candidate in a disproven
neighbourhood is skipped before any compute.

Two boundaries matter more than the pruning, and both are tested:

- `INCONCLUSIVE` gates are never learned from. That status means *never
  measured*; pruning a region because nobody looked at it would delete
  candidates on the strength of nothing.
- `BOOKKEEPING`, `DATA` and `INFRASTRUCTURE` never prune at all. A consumed
  holdout or a disk error says nothing about the parameter region.

`LOOKAHEAD` and `SAFETY` reach template-wide, being properties of generated code
rather than of the numbers fed into it.

### Experiment lineage — `apps/api/forge_api/experiments.py`

Parent/child edges, plus policy, family, hypothesis, dataset, data version, seed,
code hash, verdict, and failure classification. `ancestors`, `children`,
`descendants`, `roots`, `lineage`. Schema changes migrate by adding columns, so
an existing workspace is not thrown away — covered by a test that builds the old
four-column schema, puts a real row in it, and asserts the row survives and links
to new children.

### Preregistration that can fail — `_freeze_preregistration`, `_preregistration_holds`

The claim is frozen before the backtest and re-derived at judge time; the hashes
diverge if the hypothesis or the parameters moved in between. Fails closed on a
missing record.

### Evidence dossier — `apps/api/forge_api/dossier.py`, `apps/web/src/views/Evidence.tsx`

One document answering "why is this trusted, or not": spec and cost model,
backtests, verdict with gates split into *failed* versus *never measured*,
validation evidence, provenance and lineage, related failures, specialist
dissent, and every limitation the judge attached to a number. It assembles and
never computes. Absent sections state a reason rather than rendering zeroes.

### Specialist dissent derived from the verdict — `packages/forge/agents/debate.py`

Four positions computed from real gates and metrics, disagreement preserved.
Replaces four hardcoded sentences with fixed confidences that were served for
every run, from an endpoint that judged a hardcoded P&L series regardless of
which run was named.

### Agent surface over the new records

`list_experiments`, `experiment_lineage`, `research_memory`, `strategy_dossier`,
all read-only, plus five endpoints. UI, HTTP, agent and MCP share one
implementation.

---

## Improved

### Trial adequacy — the most important correctness change

The estimators were already right; what they were fed was not. The engine handed
the validation stack **two configurations** — one value and one neighbour on one
axis — so the Deflated Sharpe took `V[SR]` from two numbers and PBO ranked the
in-sample winner against a single rival, while `trial_count` could be in the
thousands.

G5 and G11 now require 8 distinct configurations and report `INCONCLUSIVE`
otherwise, distinguishing *absent* from *inadequate* in the observed value. The
engine builds a real neighbourhood (9 configurations). Cost is linear:
`run_validation` backtests each configuration once and reuses the `T×N` matrix.

### Dead state removed

`_constraints` (written, displayed, never consulted), `_lineage_failures` (never
read), `_retired_lineages` (never touched). The engine docstring claimed
neighbourhood pruning and lineage retirement; the first is now true and the
second claim is gone rather than left standing.

### Leaked SQLite connections closed

`with self.connect() as db:` ends the transaction but does not close the handle.
Every mission save, experiment write and memory write leaked one. Fixed in five
modules.

---

## Remaining gaps

Stated plainly, because a report that only lists wins is not useful.

1. **The orchestrator test flake is unresolved.** Diagnosed, not fixed. Thread
   stacks captured mid-hang show no `job-mission` thread at all while the API
   still reports the mission `running` with `finished_at: null` — the worker's
   terminal state is being lost, not blocked on. Pre-existing (fails at
   `0696ba2`). **A run is only a regression signal if something other than
   `tests/api/test_orchestrator.py` fails.**
2. **Validation is still gated on profitability** (`net_pnl > 0 and trades >= 30`).
   Defensible as compute economy, but it conditions the selection-integrity
   matrix on in-sample profitability — the very thing PBO exists to detect.
3. **Mutating agent parity is still absent.** Read-only coverage improved from
   13 to 17 actions against 63 routes; no agent can run validation, judge, or
   evaluate prop rules through a bounded verb.
4. **Runs are not reproducible, only tamper-evident.** See the Auto-Quant
   comparison below.
5. **No Experiments or Runs views.** Lineage is visible inside Evidence; there is
   no browsable experiment graph.
6. **`DecisionMemory` and `ArraySweepEngine` are still unwired.** Both are still
   imported only by tests. `ArraySweepEngine` is a toy that thresholds a returns
   array and never touches a real strategy; it should probably be deleted.
7. **`oracles` is a capability probe, not an oracle.** It reports whether
   `nautilus_trader` is installed. Nothing independently evaluates execution
   realism.
8. **The seeded demo run still judges a sample P&L series**, because `RunRecord`
   stores contracts, not trade series. Now labelled in the response meta rather
   than presented as analysis of real trades.

---

## Validation confidence

| Area | Confidence | Basis |
|---|---|---|
| Statistics (PSR/DSR/PBO/permutation) | **High** | Pre-existing, correct, cited; verified by reading against the papers |
| Trial adequacy gating | **High** | 10 tests incl. a regression guard for the 2-point grid; 55 grid tests over every shipped template |
| Research memory | **High** | 29 tests incl. restart durability, and that bookkeeping never prunes |
| Experiment lineage | **High** | 16 tests incl. old-schema migration, broken and self-referential parent links |
| Preregistration | **High** | 11 tests; fails closed on missing records |
| Dossier | **Medium** | 13 tests, but only against strategies with no backtest; the populated path is verified by hand, not by fixture |
| Evidence view | **Medium** | Renders correctly in the running app; 10 web tests, no populated-dossier fixture |
| Dissent | **Medium** | 17 tests assert positions track evidence; the wording is not itself validated |
| Orchestrator/missions | **Low** | Known intermittent failure, root cause open |

Nothing here has been calibrated against a live execution venue. Every P&L in
this system remains modelled.

---

## Architecture

Unchanged in shape, and deliberately so. AlgoForge is still a local-first
desktop app whose Electron shell owns a FastAPI process, with a deterministic
judge at the centre and a bounded action registry as the only way in.

What changed is that three layers which previously existed only in memory or in
control flow now exist on disk:

```
candidate → prereg frozen → experiment row (parent, policy, seed, dataset)
                              ↓
                     backtest → validation (9 configs) → judge (14 gates)
                              ↓
        failure classified → research memory → prunes the next candidate
                              ↓
                          dossier ← lineage, dissent, limitations
```

The loop closes now. Before, the arrow from failure back to the next candidate
did not exist.

---

## Reference comparison

All three repositories were cloned and read, not skimmed.

### Auto-Quant V2 — *runs as self-contained, content-addressed directories*

Its strongest idea is concrete and I underestimated it before reading the code.
A run is a directory that **copies its own inputs**, including
`inputs/judge-sources/*.py` — the judge's actual source — and hashes every file
into `manifest.json`, alongside `identity.json` carrying `datasetHash`,
per-file `datasetSourceHashes` and `dependencyHash`.

That makes a run *reproducible*: you can re-execute it years later with the
exact judge code and data that produced it.

AlgoForge is **tamper-evident but not reproducible**. It stores a `code_hash`
and a split receipt, so it can detect that something changed — but it does not
snapshot the judge, the templates, or the data, so it cannot reconstruct the
original computation. **Adopted:** nothing yet. This is the single highest-value
idea not yet taken, and it is #1 in next steps.

**Not adopted:** Auto-Quant's project/study/session hierarchy. AlgoForge's
mission → experiment → strategy line already covers the same ground with fewer
concepts, and 256k LOC of workspace machinery is not a model to import.

### Qanat — *statically enforced stage contracts*

`qanat check` refuses to serve a project that breaks the contract: nothing writes
into a `raw` stage except a source, data only moves forward (so a scheduled
pipeline cannot depend on when it last ran), every table read has a producer, one
step writes one weights table, no alpha reads another alpha's weights. Failures
are *refusals at check time*, not errors at the next scheduled run.

AlgoForge has the equivalent instinct in its static strategy guard and its
evidence tiers, but no whole-pipeline check. **Adopted in spirit:** the trial
adequacy gate is a check-time refusal of the same kind — the judge now declines
to answer rather than answering from inadequate input.

**Not adopted:** the DAG/stage model itself. AlgoForge's pipeline is fixed
(candidate → backtest → validate → judge → prop), not user-authored, so a general
dependency graph would be machinery without a user.

Qanat's point-in-time universe warning — a symbol list without membership dates
is "today's list applied to the past" — has no direct analogue here because
AlgoForge trades single instruments, but the principle already lives in G0.

### WSB-Alpha-System — *statistical discipline and preregistration*

Its trial ledger persists a content-hashed row per experiment and counts N
honestly, losers included. Its `freeze_preregistration` writes a spec document
and **raises rather than overwrite** an existing one.

Reading that is what exposed AlgoForge's G1 as a rubber stamp, and the fix in
this branch is directly attributable to it. **Adopted:** preregistration as a
gate that can fail.

Where AlgoForge is **ahead**: WSB's DSR is the normal-returns variant (skew 0,
kurtosis 3); AlgoForge's PSR/DSR carries the full skew and kurtosis correction
and refuses to claim significance when the estimator's denominator goes
non-positive. AlgoForge also runs CSCV/PBO and CPCV, which WSB does not.

**Not adopted:** its Reddit/sentiment ingestion, its live paper-broker path, and
its evolution loop, all of which are outside AlgoForge's paper-only posture.

---

## What remains uniquely AlgoForge

Not one of the three reference systems has:

- A **three-valued judge** where absent evidence yields `INCONCLUSIVE` rather
  than a fail or a pass. This is the single best idea in the repository and it
  predates this work.
- A **burn-once holdout** sealed per lineage, with a code-hash re-check that
  refuses a strategy edited after validation.
- **Failure classes with declared reach** — a taxonomy where each class states
  how far its evidence generalises, and where bookkeeping failures are
  explicitly barred from pruning anything.
- **Prop-firm validation as a separate question.** "Does this survive Topstep's
  rules?" and "is this scientifically valid?" are kept apart on purpose.
- **Calculation traces** attaching a formula and its limitations to every metric.
- A **local-first, paper-only** posture with no live-order path at all.

---

## Next highest-value work

1. **Snapshot runs into content-addressed directories** (Auto-Quant's idea).
   Copy the judge source, template source and data identity into the run, and
   hash a manifest. This turns tamper-evidence into reproducibility and is worth
   more than any further statistic.
2. **Fix the orchestrator flake**, root cause first. A suite that fails randomly
   cannot certify anything else in this list.
3. **Stop gating validation on profitability**, or record explicitly that the
   trial matrix is conditioned — otherwise PBO is measuring a filtered
   population.
4. **Delete `ArraySweepEngine` and wire or delete `DecisionMemory`.** Dead code
   that looks like a feature is worse than no code.
5. **Give the agent mutating parity** for validate/judge/prop through bounded
   verbs, so a mission can carry a candidate the whole way.
6. **An Experiments view** — the lineage graph is now on disk and only visible
   one candidate at a time.
7. **Make the oracle real** or rename it. A capability probe called an oracle
   invites the reading that something independently checks execution realism.
8. **Widen the validation grid adaptively** — 9 configurations clears the bar but
   is still thin for PBO; spend more on candidates that survive G4.

---

## On production readiness

Not production ready, and this branch does not move it closer to being so. It is
a **research instrument** that is now considerably more honest about what it
does and does not know. Every P&L is modelled; no number here has been
reconciled against NinjaTrader's Strategy Analyzer, which remains the gate that
matters before any of it is trusted.
