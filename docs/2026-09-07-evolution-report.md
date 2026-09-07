# AlgoForge evolution — report

Branch `feat/industry-standard-validation`, merged to `main`.
`da92bf6` → `7b3dc5f`, 2026-09-07. Tests 258 → 453.

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

## Second pass — merged to `main`

The branch was fast-forwarded onto `main` (no divergence, nothing lost) and the
next-steps list was then worked on `main` itself. Tests 416 → 453.

### 1. Runs are now reproducible — `packages/forge/provenance/`

The top recommendation, adopted from Auto-Quant. Each judged run gets a snapshot
holding the verdict, an identity record (dataset and version, bar count, split
receipt, seed, parameters, catalogue version, code and spec hashes, cost model),
the frozen preregistration, and a manifest with a SHA-256 per file.

Sources are **content-addressed** rather than copied per run. Auto-Quant copies
its judge sources into every run directory, which is correct but grows without
bound; addressing by content keeps the same guarantee at one copy per distinct
version. Ten thousand candidates judged by an unchanged judge store that judge
once.

`verify()` and `drift()` answer different questions on purpose — is the record
undamaged, versus have the rules moved since. A verdict from a since-edited
judge is still an honest record of what that judge decided; it just cannot be
compared with a fresh one as though they agreed.

### 2. The orchestrator flake is fixed, root cause first

`_save` evaluated `json.dumps(mission)` while building its statement but
committed at the end of the `with`. Those are different instants, and the
mission dict is mutated by the worker thread while `launch` still holds a
reference and saves it again. A thread descheduled between dump and commit could
commit a snapshot taken *before* another thread's commit; last-writer-wins then
restored an earlier state permanently.

It looked like a hang. Nothing was hung — the record had been overwritten with
an earlier version of itself, which is why the job read `DONE` while the API read
`running`. Holding the write lock across serialise-and-commit fixes it.

Evidence: a reproduction that hung at attempt 9 of 24 now completes 24 of 24, and
the test file passes six consecutive runs where it previously failed roughly one
in three. The regression test was verified honestly — it fails against the
unfixed code with the exact production symptom. **The first version of that test
did not catch the bug**, because it stalled the writer before the dump rather
than between dump and commit; the docstring records why, since the distinction is
easy to get wrong twice.

### 3. The Deflated Sharpe was deflating against the wrong spread

Found while reviewing the profitability gate. `trial_count` was
`experiments.count(scope)` — every candidate ever attempted — while
`trial_sharpes` came from the nine-point neighbourhood around the candidate being
judged. Neighbouring parameters on one template give highly correlated Sharpes,
so `V[SR]` was far too small, the best-of-N hurdle too low, and the DSR **too
generous** — biased in the permissive direction.

The engine now records each candidate's development Sharpe as it is backtested,
winners and losers alike, and deflates against that distribution. Below the
minimum count the spread is withheld rather than estimated from too few, so G5
reports INCONCLUSIVE early in a run.

This also settles the profitability gate: skipping validation for unprofitable
candidates is fine, because they fail G4 regardless and their Sharpes are now
recorded anyway. The gate is compute economy, not a selection effect.

### 4. Research memory is tamper-evident; two dead modules deleted

`DecisionMemory`'s one real idea — a hash chain — moved to `ResearchMemory`,
where it matters far more: research memory decides what the engine stops
exploring, so a silently edited failure changes what gets searched forever. Rows
now chain, and `verify_chain()` distinguishes a content edit from a deletion.
Recording is `INSERT OR IGNORE`, because replacing a row would rewrite a link the
chain depends on.

`forge.sweep` and `forge.memory.store` are deleted: both were imported only by
their own tests, and `ArraySweepEngine` thresholded a returns array without ever
touching a real strategy.

### 5. Honest naming, and an Experiments view

`forge.oracles` became `forge.capabilities`: it contained one function reporting
whether `nautilus_trader` is importable, and the name invited the reading that
something independently checks execution realism. Nothing does. The
`/api/v1/oracles` route keeps its path — the interface calls it — but its meta
now says so plainly.

The **Experiments** tab makes the lineage graph browsable: what was tried, its
outcome, and the line it came from, with failure first-class rather than styled
as an error.

---

## Remaining gaps

Stated plainly, because a report that only lists wins is not useful.

1. **Mutating agent parity is still absent.** Read-only coverage is 17 actions
   against 63 routes; no agent can run validation, judge, or evaluate prop rules
   through a bounded verb. This is the right next change and it was deliberately
   *not* rushed: the validation entry point is a large router closure with
   HTTP-specific error handling, and extracting it into a shared core is a
   refactor of the single most important code path in the system.
2. **Snapshots are not hermetic.** They do not pin the Python version, installed
   package versions, or the market data itself — data is identified by key,
   version and bar count rather than copied, because vendor archives are large
   and paid for. A snapshot makes a run auditable and re-runnable against the
   same inputs; it is not a reproducible build.
3. **The validation grid is still fixed at nine configurations.** It clears the
   adequacy floor but is thin for PBO. Spending more on candidates that survive
   G4 would beat spending equally on all of them.
4. **No Runs view.** Snapshots are readable through the dossier, but no screen
   lists them.
5. **The seeded demo run still judges a sample P&L series**, because `RunRecord`
   stores contracts, not trade series. Labelled in the response meta rather than
   presented as analysis of real trades.
6. **Nothing is calibrated.** Every P&L here is modelled. No number has been
   reconciled against NinjaTrader's Strategy Analyzer.

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
| Run snapshots | **High** | 19 tests incl. tamper, deletion, corrupt manifest, drift, and content-addressing |
| Memory hash chain | **High** | detects content edits and mid-chain deletion; legacy unchained rows still open |
| Orchestrator/missions | **High** | root cause found and fixed; regression test verified to fail without the fix |

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

Items 1, 2, 3, 4, 6 and 7 of the original list are done; what follows replaces
them.

1. **Give the agent mutating parity** for validate/judge/prop through bounded
   verbs, so a mission can carry a candidate the whole way. Requires extracting
   the validation entry point out of its router closure into a shared core —
   worth doing carefully, not quickly.
2. **Widen the validation grid adaptively.** Nine configurations clears the
   adequacy floor but is thin for PBO. Spend more on candidates that survive G4
   rather than equally on all of them.
3. **A Runs view** over the snapshot store, showing intact/drifted status, so
   the reproducibility record is browsable rather than only reachable per
   candidate.
4. **Re-run a snapshot.** The inputs are now captured; an actual
   `replay(run_id)` that re-executes against them would convert "auditable" into
   "verified reproducible".
5. **Calibrate against NinjaTrader's Strategy Analyzer.** Everything above is
   internal consistency. This is the only step that makes any number real, and
   nothing should be trusted until it happens.
6. **Prune the snapshot store.** It grows with every judged run; content
   addressing bounds the source copies but not the per-run directories.

---

## On production readiness

Not production ready, and this branch does not move it closer to being so. It is
a **research instrument** that is now considerably more honest about what it
does and does not know. Every P&L is modelled; no number here has been
reconciled against NinjaTrader's Strategy Analyzer, which remains the gate that
matters before any of it is trusted.
