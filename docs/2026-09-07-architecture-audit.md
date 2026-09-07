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

## 2. The headline defect: research memory does not survive a restart

**`apps/api/forge_api/engine.py:162-163`** `[DOCUMENTED]`

```python
self._constraints: dict[tuple[str, str], str] = {}
self._lineage_failures: dict[str, int] = {}
```

Plain instance dicts. `_remember()` writes to them; `constraints()` reads the
last 100. Nothing persists them and nothing reloads them.

The README advertises:

> Failures become constraints that skip matching candidates before any compute
> is spent.

That is true within a single process lifetime and false across restarts. Every
restart, the engine re-learns from zero and re-spends compute on parameter
regions it has already disproven. On a 2-core machine this is the difference
between a research system and a treadmill.

Compounding it:

- **`packages/forge/memory/store.py`** — `DecisionMemory`, an append-only
  hash-chained partitioned memory, is a well-built class that is **imported only
  by `tests/agents/test_agents.py`**. It is not wired into the running system at
  all. `[DOCUMENTED]`
- **`packages/forge/sweep/engine.py`** — `ArraySweepEngine` is likewise imported
  only by `tests/judge/test_judge.py`. It is also a toy: it thresholds a returns
  array and never touches a real strategy. `[DOCUMENTED]`

So AlgoForge currently has **no durable research memory**, despite three
separate components that look like one.

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

### 3.7 `oracles` is a capability probe, not an oracle

`packages/forge/oracles/nautilus.py` reports whether `nautilus_trader` is
installed and states its own limitations clearly. It is honest and useful, but
it does not evaluate anything. Prompt §18's "specialists independently assess"
is unimplemented. `[DOCUMENTED]`

---

## 4. Risk register

| # | Risk | Location | Severity |
|---|---|---|---|
| 1 | Constraints lost on restart; compute re-spent on disproven regions | `engine.py:162` | **High** |
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

1. Research memory evaporates (§2).
2. Experiments carry no lineage (§3.1).
3. The evidence that feeds the good estimators is too thin to support them
   (§3.2).

Phases B and C therefore carry the highest value, and Phase D — which the prompt
frames as the big statistical build — is mostly a matter of *feeding the
existing estimators properly* rather than writing new ones.

Ordering:

1. **B1** — durable research memory: persist constraints and failure
   classifications to SQLite, reload on start.
2. **B2** — real experiment record with parent/child lineage.
3. **C1** — widen the validation grid so PBO/DSR rest on adequate spread; make
   a degenerate grid report `INCONCLUSIVE` rather than a number.
4. **E1** — evidence dossier assembled from the durable record.
5. **F1** — extend the action registry over the evidence/lineage verbs.
6. **G1** — Lineage and Evidence views.
7. **§3.4** — either make dissent real or stop serving it as analysis.

Nothing in this plan discards existing behaviour; each step is additive or
replaces an in-memory structure with a persisted one behind the same interface.
