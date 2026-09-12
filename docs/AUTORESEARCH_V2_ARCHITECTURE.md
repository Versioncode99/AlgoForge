# AutoResearch V2 — Architecture

How AlgoForge decides what to research, on which slice of history, and what
stops a window from becoming a free retry.

Read `docs/AUTORESEARCH_V2_AUDIT.md` first for what was already here. This
document describes only what changed and why.

---

## The shift

From:

```
template -> parameter combinations -> backtest -> judge
```

to:

```
question -> hypothesis -> mechanism -> plan -> time scope -> gate
  -> pilot -> experiment -> failure -> derived hypothesis -> next plan
```

The engine's unit of work was a `StrategySpec`: a template, some parameters, a
backtest. That is a thing to **run**, not a thing to **ask**. A proposal with no
stated prediction cannot be wrong, so its result cannot teach anything, so a
campaign accumulates candidates rather than knowledge.

## The temporal reservoir

### What was actually happening

Not brute force over sixteen years. The opposite:

```python
# engine.py
self._loaded = self.market.load(self.state.config.dataset, limit=self.state.config.max_bars)

# market.py
window = frame.tail(limit) if limit and limit < len(frame) else frame
```

`max_bars` defaults to 250,000 ≈ **nine months** at ~347,760 one-minute bars a
year, always the most recent nine months, loaded once and shared by every worker
and every experiment in every campaign. `Campaign.start_date` and `end_date`
were stored, API-exposed and read by nothing.

### `TimeScope`

`packages/forge/research/timescope.py`. A selection from a reservoir, with the
reservoir kept beside it — an experiment that used two years out of sixteen made
a choice, and a record showing only the two years has lost the choice.

| Field | Holds |
|---|---|
| `available_start` / `available_end` | The reservoir, measured from the archive |
| `selected_start` / `selected_end` | What this experiment runs on |
| `method` | `RECENT_N_YEARS`, `FIXED_DATE_RANGE`, `FULL_AVAILABLE_HISTORY`, `ROLLING`, `ANCHORED`, `REGIME_SELECTED`, `CROSS_REGIME`, `EVENT_SELECTED`, `CUSTOM` |
| `rationale` | Why this window, for this hypothesis. 40 characters minimum |
| `windows` | Named spans: `train`, `validation`, `holdout`, `fold-3-test`, `regime-2` |
| `segments` | The regimes or events a selecting method selected on |
| `alternatives_considered` | What it was chosen instead of — exposure is only countable if recorded |

`coverage` is the share of the reservoir used, which is what makes "two years of
sixteen" a visible decision rather than an invisible default.

Refusals: a window reaching outside the reservoir, a holdout overlapping the
train span, a validation window starting before training ends, a walk-forward
longer than the data, a regime selection that does not name its regime, a
window with no stated reason.

**No minimum window length**, deliberately. Twenty days of one-minute bars is
~6,000 observations and is plenty; twenty days of daily bars is twenty and is
useless. Sufficiency is a bar count and this module does not read data, so the
count is checked where it can be counted — `MarketService.load_scope(minimum_bars=)`
and `chronological_split`'s existing `INSUFFICIENT_SPLIT_BARS`.

### Freezing, without a new gate

This is the part worth reading twice.

`Preregistration` already freezes `hypothesis + mechanism + falsification`,
content-hashes them, stores them append-only, and **G1 re-derives the hash at
judge time and fails `CLAIM_MOVED`** when anything shifted.

So the window goes *into that payload*:

```python
if scope_fingerprint:
    payload["time_scope"] = scope_fingerprint
```

"Ran two years, disliked the answer, reported the five-year number" is now a G1
failure. No second freeze, no new gate, no change to the ladder.

Folded in **conditionally**. Unconditionally would change the hash of every
claim frozen before scopes existed, and G1 would report `CLAIM_MOVED` for every
strategy in the library — invalidating evidence nobody had touched.

### Selection exposure

`exposure(scopes)` counts *distinct* fingerprints. Three windows tried for one
claim is three looks at the data, and the multiple-testing correction has to see
three. Re-running the identical window is not a new look.

### Serving a scope

`MarketService.reservoir(key)` measures the archive's real span —  measured, not
taken from the dataset's declared span, because a purchased archive can begin
later than its label says.

`load_scope(scope, minimum_bars=, pad_bars=)` returns the dates' bars and
refuses an empty or insufficient window rather than returning one: an experiment
that ran on nothing must not look like one that ran.

`load_window(scope, role)` serves one named span.

## The plan

`packages/forge/research/plan.py`. `ResearchPlan` holds the question, the claim,
the mechanism, the temporal design, the costs, the features, the parameters and
what would count as success or failure — fixed before any number exists.

### The gate

`review(plan, campaign=, exhausted_fingerprints=)` is **deterministic and
consults no model**. A model may write a plan; nothing a model says can make one
pass. Same boundary the judge has, for the same reason.

| Status | Means |
|---|---|
| `PLAN_ACCEPTED` | Run it |
| `PLAN_REJECTED` | It could not produce evidence whatever it returned |
| `PLAN_BLOCKED_DATA` | The research is sound; the data is absent. **Kept**, because it becomes runnable when the data arrives, and nothing is substituted meanwhile |
| `PLAN_REQUIRES_REVIEW` | Defensible and consequential. A person decides |

Blocking findings: a prediction naming no observation; success or failure
criteria left undefined; parameters that may still move; capabilities the
installation cannot serve; an identical plan already settled.

Review findings: consuming >95% of the reservoir with no pilot; a cross-regime
plan that names no regimes; a campaign that has already stopped.

The `REJECTED` / `BLOCKED_DATA` split matters: research that could never work is
discarded, research whose data is missing is shelved. Collapsing them loses the
question.

## Pilots

`PilotDesign` carries **its own** time scope. Hypothesis-aware by construction
rather than by a rule like "two years first for everything" — a claim about a
macro release needs the releases in its pilot window; a microstructure claim
does not. At most eight configurations, because a pilot that searches is a cheap
experiment with an inflated trial count.

`promotion(outcome)`:

| Outcome | Promotes | Why |
|---|---|---|
| `PROMISING` | yes | |
| `INCONCLUSIVE` | **yes** | A pilot is small by construction. "Could not tell" is the expected answer for a real effect on a short window; refusing it rejects exactly the hypotheses a pilot is too small to see |
| `NO_SIGNAL` | no | It looked and found nothing where something was predicted |
| `IMPLEMENTATION_FAILURE` | no | The strategy did not run, so the pilot says **nothing** about the hypothesis. Explicitly not a negative result |
| `BLOCKED` | no | |

**A pilot is never evidence.** It answers "is this worth more compute", which is
a question about the research budget. Whether an edge exists is the judge's
question, asked of a full experiment.

## The engine

`scope_for(worker)` resolves the worker's campaign through the director — which
already holds the campaign store, so there is no second source of truth about
which campaign a worker serves — and returns its `TimeScope`, or `None`.

`None` is the ordinary answer and preserves existing behaviour exactly. Every
campaign configured before this change runs as it always did.

`_bars_for(worker, default)` is resolved **per cycle**, not once per run,
because a worker may be serving a different campaign than it was last cycle.
Bars are cached by scope fingerprint, so two campaigns selecting the same window
share one load and one split. The frame is sliced outside the data lock: inside
it would serialise the whole engine behind one campaign's first cycle.

Degradations are recorded, never silent:

- a window that cannot be loaded → falls back to the run's window **and logs it**;
- a window too small to partition → logged as a fact about the window;
- an incoherent range → falls back (a misconfiguration is not a research emergency);
- a range reaching before the archive → clamped, with both ends kept so the
  clamp stays inspectable.

## Registry verbs

Four, all non-mutating: `describe_reservoir`, `propose_time_scope`,
`scope_exposure`, `review_research_plan`. Registry now 154 actions.

They compose: `propose_time_scope`'s payload feeds `review_research_plan`.
`TimeScope` forbids extra fields on purpose — a typo in a window should be
refused, not silently dropped — so `TimeScope.DERIVED` names the computed keys
and `from_payload` drops them, rather than the rule being relaxed.

## What is not built

Stated here so the document cannot be read as a claim:

- **No plan store.** Plans are constructed, gated and preregistered; they are
  not yet persisted in their own table, so a plan's lineage lives in the
  hypothesis graph and the journal rather than in a plan record.
- **The engine does not yet build plans.** `_cycle` still proposes a
  `StrategySpec` through the director. The plan and gate are reachable from the
  registry and are tested end to end, but the autonomous loop has not been
  rewritten to go through them.
- **Pilots are not executed.** The design, the outcomes and the promotion rule
  exist and are tested; nothing runs a reduced backtest yet. Note that the
  engine *does* already screen: `_cycle` runs the development partition first
  and rejects on `net_pnl <= 0 or trades < 30` before validation is touched.
  That is a cheap gate before an expensive path — it is simply not a *temporal*
  pilot, because it uses the same window rather than a smaller one.
- **The plan critic (§9) is not built.** `agents/debate.py` critiques a verdict;
  nothing critiques a plan before compute is spent.


## A defect this design introduced, and what it cost to find

Worth recording, because it is the exact failure mode the whole feature exists
to prevent, reintroduced by the feature.

`ResearchPartitions` holds actual bar **lists**. `_cycle` resolved them as
`self._partitions or chronological_split(bars, ...)`, and `self._partitions` is
cut from the run's default window. So the first version of the engine wiring
handed a cycle scoped bars and left it backtesting the **default** window's
development partition, reporting the result against the scope — the window
silently ignored, on real data only, with nothing on screen to show it.

It hid from all eight tests written for the feature because the synthetic
dataset is not `is_real`, so the partitioned path never ran.

The fix threads the partitions with the bars. The regression test needed
strengthening twice: the first version called `_bars_for` once, which returns
through the cache-*miss* branch, and re-introducing the leak in the cache-*hit*
branch left it passing. It now loads twice, and was confirmed to fail with the
leak in place and pass without it.

Two lessons that generalise beyond this change:

1. **A fixture that cannot reach a code path cannot test it.** `is_real` gates a
   whole arm of the cycle, and every test for a data-path feature was written
   against a fixture that takes the other arm.
2. **A cache has two return paths and a test usually exercises one.** Any
   assertion about what a cached function returns has to load twice.
