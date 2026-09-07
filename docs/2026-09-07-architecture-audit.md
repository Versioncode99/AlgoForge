# AlgoForge architecture audit — Phase A

Branch: `feat/industry-standard-validation` @ `da92bf6`
Date: 2026-09-07
Scope: 15,257 LOC Python (`packages/forge`, `apps/api`), 4,763 LOC TypeScript (`apps/web`)

Method: execution tracing, not README reading. Every claim below was checked by
following imports and call sites. Confidence tags follow the vault convention:
`[DOCUMENTED]` = verified in code, `[INFERRED]` = strongly implied but not
directly executed, `[UNCONFIRMED]` = plausible, unverified.

---

## 1. What is genuinely excellent and must be preserved

### The judge gate ladder — `packages/forge/judge/engine.py` `[DOCUMENTED]`

Fourteen gates, G0–G13. The critical design decision is the three-valued
outcome: absent evidence yields `INCONCLUSIVE`, never `PASS`. `_decide` fails on
any `FAIL`, withholds on any `INCONCLUSIVE`. A strategy that was never
walk-forwarded is "an unanswered question", not a borderline pass.

Every metric carries a `CalculationTrace` with its formula and explicit
limitations, including the standing note that fills are modelled, not
calibrated. This is more honest than most commercial platforms.

### The statistics — `packages/forge/judge/statistics.py` `[DOCUMENTED]`

Correct implementations of the published estimators, properly cited:

- **PSR** (Bailey & López de Prado 2012) with skew/kurtosis correction, and it
  returns 0.0 rather than claiming significance when the denominator goes
  non-positive.
- **DSR** (2014) with the Gumbel best-of-N hurdle.
- **PBO via CSCV** (2014) — and the average-rank tie handling is right, so an
  exactly-median winner scores neutral rather than overfit.
- **Permutation p-value** with the `+1/+1` correction, so it never reports p=0.

Two conventions are enforced throughout and documented in the module docstring:
per-period vs annualised Sharpe are never mixed, and kurtosis is non-excess.
Mixing either is the usual way these numbers get quietly corrupted. `t_statistic`
is named honestly rather than being passed off as a Sharpe.

This is the strongest asset in the repository.

### The validation runner — `packages/forge/research/validation.py` `[DOCUMENTED]`

Re-runs the parameter search **inside every fold and every split**. The
docstring states the reason precisely: selecting once on the full series and
then measuring folds of that winner reports the stability of a number that
already saw everything. Getting this right is rare.

### Bounded action registry — `apps/api/forge_api/actions.py` `[DOCUMENTED]`

13 named actions with declared schemas, each validating its own arguments. No
"run arbitrary code" verb. Refusals are explicit and carry reasons.
`apps/api/forge_api/mcp_server.py` is a genuine facade over the same registry —
it derives its tool list from `actions.schemas()` and honours the `mutating`
flag, so read-only really is read-only. Agent/UI parity is architecturally real
here, though incomplete (§3).

### Burn-once holdout — `apps/api/forge_api/engine.py` `[DOCUMENTED]`

The engine re-checks `library.code_hash` against the validated hash before
opening the holdout, and `research_ledger.consume` makes the holdout burn once
per lineage. A strategy edited after validation cannot reach the sealed data.
This is a real anti-leakage mechanism, not a label.

### Prop isolation — `apps/web/src/views/PropFirm.tsx`, `packages/forge/prop/` `[DOCUMENTED]`

Prop-firm validation is already a separate module and a separate tab, not a
mandatory research stage. Prompt §20 is satisfied as-is.

---

## 2. The headline defect: constraint learning does not gate anything

The README advertises:

> Failures become constraints that skip matching candidates before any compute
> is spent.

Tracing what actually skips a candidate shows something different.

**The real gate is `experiments.reserve()`** — `engine.py:454` `[DOCUMENTED]`

```python
attempt_id = self.experiments.reserve(self._scope(), template_key, params)
if attempt_id is None:
    self._bump("skipped_by_memory")
```

This *is* durable: it hashes `(scope, template, parameters)` into SQLite and
refuses a duplicate insert. But it is **exact parameter-set dedup**. It cannot
skip a *region*; only a byte-identical repeat. Change one parameter by one step
and the candidate runs in full, however many neighbours have already failed.

**The reason-carrying constraint map never gates.** `engine.py:162-164`
`[DOCUMENTED]`

```python
self._constraints: dict[tuple[str, str], str] = {}
self._lineage_failures: dict[str, int] = {}
self._retired_lineages: set[str] = set()
```

- `_constraints` is written by `_remember()` and read only by `constraints()`,
  which serves the UI. **It is never consulted to skip a candidate.** It is a
  display of recent failure reasons, and it is lost on restart.
- `_lineage_failures` is written and **never read anywhere**. Dead state.
- `_retired_lineages` is initialised and **never touched again**. Dead state.
- `_remember(self, key, lineage, reason)` — the parameter is named `lineage`,
  but all four call sites (581, 664, 686, 727) pass `template_key`. So the
  counter that is never read is also not counting what its name says.

So the advertised mechanism is half-built: the cheap exact-match skip is real
and durable, and the *learning* half — generalising a failure into a constraint
that prunes a region — does not exist. Failure reasons are collected, shown,
and discarded.

This is the highest-value gap in the repository, and it is the one the prompt's
§6 and §12 are entirely about.

Compounding it:

- **`packages/forge/memory/store.py`** — `DecisionMemory`, an append-only
  hash-chained partitioned memory, is a well-built class that is **imported only
  by `tests/agents/test_agents.py`**. It is not wired into the running system at
  all. `[DOCUMENTED]`
- **`packages/forge/sweep/engine.py`** — `ArraySweepEngine` is likewise imported
  only by `tests/judge/test_judge.py`. It is also a toy: it thresholds a returns
  array and never touches a real strategy. `[DOCUMENTED]`

So the only durable memory AlgoForge has is an exact-match attempt table, while
three separate components that look like research memory — the constraint map,
`DecisionMemory`, and `ArraySweepEngine` — contribute nothing to the running
system.

---

## 3. Gaps against the target architecture

### 3.1 `Experiments` is an attempt-dedup table, not an experiment record

**`apps/api/forge_api/experiments.py`** `[DOCUMENTED]`

Schema: `(id TEXT PRIMARY KEY, scope TEXT, template TEXT, payload TEXT)`.

Against the fields the target model requires, this carries roughly 15%. Absent:
parent experiment, parent strategy, hypothesis, objective, dataset identity,
data version, time range, seed, code identity, run contract reference, failure
gate, failure reason, learned constraint, evidence artifacts, timestamps,
lineage edges.

`reserve()` deduplicates by hash and `finish()` merges a JSON blob. There is no
parent/child edge anywhere, so the lineage graph in prompt §7 cannot be
constructed from this table.

### 3.2 The evidence feeding G5 and G11 is statistically degenerate

**`apps/api/forge_api/engine.py:594-599`** `[DOCUMENTED]`

```python
grid = {name: [value] for name, value in params.items()}
axis = template.parameters[0]
neighbour = params[axis.name] + axis.step
...
grid[axis.name] = sorted({params[axis.name], float(neighbour)})
```

The validation grid is **two points on a single axis**. Consequences:

- `probability_of_backtest_overfitting` receives a `T x 2` matrix. It runs (the
  guard only demands N≥2), but PBO over two configurations is close to
  meaningless — the "selection procedure" being tested is a coin flip.
- `trial_sharpes` has two elements, so `V[SR] = var(2 samples, ddof=1)`. That
  variance then sets the best-of-N hurdle in `expected_max_sharpe`, where N is
  `experiments.count(scope)` — potentially thousands.

So the deflation hurdle is built from a two-sample variance estimate applied to
a thousand-trial selection. G5 and G11 therefore produce confident-looking
numbers resting on almost no spread. The estimators are correct; the inputs are
not adequate. This is the clearest "results can be trusted incorrectly" risk in
the system.

Note the judge already guards the *absent* case honestly
(`_deflation_measurable` returns `INCONCLUSIVE` when trial Sharpes are missing).
The problem is the *present but degenerate* case, which currently reads as
measured evidence.

### 3.3 Validation is gated on profitability

**`apps/api/forge_api/engine.py:588`** `[DOCUMENTED]`

```python
if partitions is not None and result.net_pnl > 0 and len(result.trades) >= 30:
```

Losing candidates never get validation evidence. Defensible as a compute
optimisation (they fail G4 regardless), but it means the trial matrix used for
selection-integrity testing is itself conditioned on in-sample profitability —
a subtle selection effect that PBO is specifically meant to detect.

### 3.4 Agent dissent is fabricated

**`packages/forge/agents/debate.py`** `[DOCUMENTED]`

`build_demo_debate()` returns hardcoded claims with fixed confidences (0.62,
0.91, 0.88) and fixed statements, regardless of the run or verdict. It is
labelled `DEMO_NARRATIVE`/`RESEARCH_ONLY` internally — honest — but it is served
from `GET /api/v1/agents/{run_id}` in `main.py:192-209` as though it were
analysis of that run.

Prompt §18 lists "agent dissent" as an AlgoForge differentiator to preserve and
make meaningful. Today there is nothing to preserve: it is a fixture.

### 3.5 Agent/UI/MCP parity is partial

13 registered actions against 63 HTTP routes `[DOCUMENTED]`. Verbs with no
action surface include: validation, judging, prop evaluation, experiment
inspection, lineage, evidence retrieval, settings, dataset management.

The architecture is right; the coverage is not. An agent cannot currently ask
"what evidence supports this candidate?" through the bounded surface.

### 3.6 No lineage, evidence or experiment views

`apps/web/src/views/` contains Overview, Engine, Pipeline, Strategies,
ResearchLab, ValidationLab, Orchestrator, AgentCommand, PropFirm, Settings.

Absent: Lineage, Evidence/dossier, Experiments, Runs, ForgeKeeper. ForgeKeeper
has a service (`packages/forge/forgekeeper/service.py`, 141 LOC, wired into
`main.py`) but no interface. `[DOCUMENTED]`

### 3.7 The orchestrator tests are flaky, which will mask real regressions

`tests/api/test_orchestrator.py` fails intermittently — a different subset each
run, sometimes none. Verified pre-existing: it fails at `0696ba2`, before any
change in this branch's audit work. `[DOCUMENTED]`

Diagnosis. The symptom is a step stuck in `running` with `job_id: None` for
`create_strategy` on an unknown template — an action that refuses immediately
(`actions.py:477`). `Actions.call` records the refusal and then re-raises it as
an `ActionError` (`actions.py:151-152`), so the step should fail within
milliseconds either way. It cannot be blocked on the action itself.

What it is blocked on is CPU. `forge_api.jobs.REGISTRY` is a module-level global
with no teardown, and each `submit` starts a daemon thread. Tests that launch
backtest jobs leave those threads running into subsequent tests; on a 2-core
machine they starve the mission thread past the 60s wait in the test's `wait()`
helper. It passes in isolation and fails under load, which is the signature.

This matters beyond tidiness: nothing cancels orphaned jobs in production
either. A suite that fails randomly also cannot be used to detect regressions,
which is the standard prompt §28 sets.

### 3.8 `oracles` is a capability probe, not an oracle

`packages/forge/oracles/nautilus.py` reports whether `nautilus_trader` is
installed and states its own limitations clearly. It is honest and useful, but
it does not evaluate anything. Prompt §18's "specialists independently assess"
is unimplemented. `[DOCUMENTED]`

---

## 4. Risk register

| # | Risk | Location | Severity |
|---|---|---|---|
| 1 | Constraints never gate; only exact repeats are skipped, so disproven regions are re-explored | `engine.py:162`, `engine.py:454` | **High** |
| 1b | Three pieces of engine state dead or misnamed (`_lineage_failures`, `_retired_lineages`, `_remember`'s `lineage` arg) | `engine.py:162-164, 739` | Low |
| 2 | PBO/DSR computed from a 2-point grid, presented as measured | `engine.py:594` | **High** |
| 3 | No experiment lineage; a strategy's origin is unreconstructable | `experiments.py` | **High** |
| 4 | Fabricated dissent served from a real endpoint | `debate.py` | Medium |
| 5 | Validation conditioned on in-sample profitability | `engine.py:588` | Medium |
| 6 | 50 of 63 API verbs unreachable by agent/MCP | `actions.py` | Medium |
| 7 | Two well-built modules unwired (`memory`, `sweep`) | — | Low (waste) |

---

## 5. What this means for the plan

The system's *scientific core is already strong* — stronger than the prompt
assumes. The estimators, the gate ladder's fail-closed semantics, the
fold-internal parameter search and the burn-once holdout are all correct.

The weakness is **durability and provenance**, not statistics:

1. Constraint learning collects reasons but never prunes (§2).
2. Experiments carry no lineage (§3.1).
3. The evidence that feeds the good estimators is too thin to support them
   (§3.2).

Phases B and C therefore carry the highest value, and Phase D — which the prompt
frames as the big statistical build — is mostly a matter of *feeding the
existing estimators properly* rather than writing new ones.

Ordering:

1. **B1** — make constraints real: a persisted, queryable failure record that
   classifies each failure and is *consulted before compute*, pruning
   neighbourhoods rather than only byte-identical repeats.
2. **B2** — real experiment record with parent/child lineage.
3. **C1** — widen the validation grid so PBO/DSR rest on adequate spread; make
   a degenerate grid report `INCONCLUSIVE` rather than a number.
4. **E1** — evidence dossier assembled from the durable record.
5. **F1** — extend the action registry over the evidence/lineage verbs.
6. **G1** — Lineage and Evidence views.
7. **§3.4** — either make dissent real or stop serving it as analysis.

Nothing in this plan discards existing behaviour; each step is additive or
replaces an in-memory structure with a persisted one behind the same interface.

---

## 6. Status log

Updated as the plan is worked through. Nothing is marked done without a passing
artifact gate, per the Definition-of-Done rule.

| Item | State | Evidence |
|---|---|---|
| §2 constraint learning never prunes | **Done** | `forge/memory/research.py`; engine consults it before `Experiments.reserve`; 22 store tests + 7 engine tests, including restart durability |
| §1b dead engine state | **Done** | `_constraints`, `_lineage_failures`, `_retired_lineages` removed; `constraints()` served from disk |
| §3.2 degenerate G5/G11 evidence | **Done** | `MINIMUM_TRIAL_CONFIGURATIONS = 8` in the judge; engine grid widened to 9 configurations; 10 adequacy tests + 55 grid tests over every shipped template |
| §3.1 experiment record has no lineage | **Done** | parent/child edges written by the engine; `ancestors`/`children`/`descendants`/`roots`; 16 tests incl. old-schema migration |
| §3.3 validation gated on profitability | Open | — |
| §3.4 fabricated dissent | **Done** | `build_debate(verdict)` derives all four positions from real gates/metrics; 17 tests incl. a guard against the old fixed constants |
| §3.5 13 of 63 verbs have an action | **Partly** | +3 read-only actions (`list_experiments`, `experiment_lineage`, `research_memory`) and 4 endpoints; mutating parity still absent |
| §3.6 no lineage/evidence/experiment views | Open | — |
| §3.7 orchestrator test flake | Open | diagnosed above, not yet fixed |

### Test baseline

258 at `da92bf6` → 394 now. The 1–2 intermittent failures are always the
orchestrator flake in §3.7; a run is only a regression signal if something
*other* than `tests/api/test_orchestrator.py` fails.

### A note on test isolation `[DOCUMENTED]`

`create_app()` resolves its workspace with `resolve_workspace(ROOT)`, which
falls back to the repository's own `data/` directory. Tests that construct an
app without setting `ALGOFORGE_VAULT` therefore share state with each other and
with whatever real workspace exists on the machine running them — writes land
in `data/experiments.db` and `data/research_memory.db` for real.

`tests/api/test_api.py` passes a database path but not a workspace, so it is
affected. New tests here set `ALGOFORGE_VAULT` to a `tmp_path`, which is the
documented first step of the resolution order. Worth applying to the older API
tests too.

### Correction to §3.4, found while replacing it `[DOCUMENTED]`

The endpoint was worse than first recorded. It did not merely serve fixed
claims: `main.py` judged a **hardcoded P&L series** — `(80, -25, 95, -30, 70,
-20, 110, -35, 60, 45, -15, 85) * 3` — regardless of which `run_id` was
requested, and then passed the resulting verdict to `build_demo_debate`, which
used only its id. Fabricated series, fabricated verdict, fixed claims, served
under a real run's identifier.

`RunRecord` stores run contracts (hashes, tier, labels), not trade series, so
there was no real P&L to judge. The sample series is therefore still used for
the seeded demo run, but the response now says so in its meta, and the
specialist positions are derived from the verdict rather than scripted.
