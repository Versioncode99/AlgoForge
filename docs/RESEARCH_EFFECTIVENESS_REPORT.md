# Research Effectiveness Report

**The question:** is AlgoForge actually discovering new strategies, or is it
mostly generating variations of existing ones?

**The short answer:** it genuinely creates new *constructions* — measured at 1.3
parameter configurations per construction, which is close to the floor. It is
not a parameter-variation engine. But it currently learns very little from what
it runs, and two-thirds of its cycles produce nothing at all.

Everything below is measured from a bounded campaign driven through the real
engine, director, campaign store, frontier and hypothesis graph. Method and
limits are in §5, and they matter.

---

## 1. The measurement

120 cycles, 4 workers, one campaign, synthetic dataset, 12,000 bars.

```
cycle outcomes      PROGRESS 34 · NOT_NOVEL 81 · DUPLICATE 5
experiments         39
hypotheses          11        families created  9
templates created   11        follow-ups        1
validated           0
```

## 2. Is it discovering, or varying?

**Discovering.** This is the clearest result in the run.

```
strategy specs written        29
distinct templates behind them 22
distinct families              8
→ 1.3 parameter configurations per construction
```

A parameter-variation engine would show a high ratio here — "149 strategies"
standing on twenty constructions is 7.5. AlgoForge shows **1.3**, which is
nearly one construction per strategy.

Nine of the eleven templates created in the run were *generated* ones
(`gen_displacement_reversion`, `gen_trend_strength_gate`, `gen_volume_shock`,
`gen_range_compression_release`, …), composed by the director rather than drawn
from the shipped catalogue. It created a new family — `discovered_displacement_reversion`
— during the run.

Mechanism diversity is genuine: **11 hypotheses, 11 distinct mechanisms**. Not
one repeat.

So the answer to the brief's most important question is favourable, and it is
favourable on the measure that would expose the opposite.

## 3. Where it is weak

### 3.1 Two-thirds of cycles produce nothing

```
NOT_NOVEL  81 / 120 cycles   (67.5%)
  of which SAME_CONSTRUCTION  78
  EXACT_DUPLICATE              5
```

The refusals are **cheap** — they happen before any backtest, so this is not
wasted compute in the expensive sense. But it is wasted *cycles*: the director
proposes, the novelty gate refuses, and the loop goes round. Only 34 of 120
cycles advanced the research.

The skip ledger is doing its job and reporting honestly (`useful: 86,
wasted: 0`). The problem is upstream: the proposer keeps re-deriving
constructions the gate has already seen.

### 3.2 Failure produced almost no research

Eight frontier items reached `FAILED`. The run generated **one** follow-up.

Root cause found and fixed in this branch: the development screen rejected
candidates with **no failure class**, and `_generate_followups` returns
immediately on an observation carrying none. A construction that produced no
edge generated no question. See §5 for why the fix could not be measured.

Measured separately, the follow-up machinery itself is healthy: eight failures
across eight families and seven failure classes produce **six admitted**
follow-ups, two correctly refused as restatements.

### 3.3 Nothing reaches validation, and nothing sits open

```
frontier: FAILED 8 · EXHAUSTED 3 · PROMISING 0 · VALIDATED 0
          UNTESTED 0 · INCONCLUSIVE 0 · PARTIALLY_EXPLORED 0
```

Every question settled immediately, to failure or exhaustion. Nothing was left
`PROMISING` or `PARTIALLY_EXPLORED`.

On a synthetic, edge-free series **this is the correct outcome** — there is no
edge to find, and a system reporting `PROMISING` on it would be the alarming
result. It does mean this run says nothing about the promotion path, which is
untested here by construction.

## 4. Activity versus progress

| | Activity | Progress |
|---|---|---|
| cycles | 120 | 34 advanced anything |
| experiments | 39 | 8 reached a frontier verdict |
| strategies | 29 | 22 distinct constructions |
| hypotheses | 11 | 11 distinct mechanisms, 1 derived from a failure |

The gap between the columns is the honest summary: **it explores well and
learns badly.** Diversity of ideas is high; conversion of results into new
questions is low.

## 5. What this measurement cannot show, and why

This is the most important section for anyone reading the numbers above.

**The synthetic dataset cannot reach the code that matters.** `Dataset.is_real`
is `authority == "TRUTH"`; the synthetic set is `FIXTURE`. `_cycle` branches on
it, and the entire partitioned path — the chronological split, the development
backtest, the screen — lives inside `if real_data:`.

So every engine test and every bounded campaign in this repository takes the
other arm. Consequences:

1. The screen-classification fix in this branch **cannot be demonstrated by
   re-running the campaign**: the numbers come back byte-identical because the
   code never executes. It is verified by direct unit tests instead.
2. That blind spot has now hidden **two** defects in this branch alone — the
   screen gap above, and a partition leak where scoped bars would have been
   judged against the default window.
3. The 0-validated result is not evidence about the promotion path.

**There is no real archive in this environment** (`data/market/databento/` is
empty), so no measurement here is a statement about market behaviour. Every
number is about the machinery.

**One campaign, one seed, 120 cycles.** Ratios like 1.3 configurations per
construction are stable enough to report; the 67.5% refusal rate is a property
of one objective on one dataset and should not be read as a system constant.

## 6. What would raise effectiveness most

In order of expected effect:

1. **Make a real-data fixture reachable in tests.** Not for realism — for
   *coverage*. Two defects hid behind this in one branch, and the most
   consequential path in the engine is currently untested.
2. **Route the loop through `ResearchPlan`.** The plan gate refuses
   unfalsifiable and already-settled work before compute; the loop does not use
   it yet. That directly attacks the 67.5%.
3. **Give the proposer memory of refusals.** 78 `SAME_CONSTRUCTION` refusals in
   120 cycles means it is re-deriving what the gate already rejected. The skip
   ledger records every one; the proposer does not read it.
4. **Execute pilots.** The promotion rule and designs exist; nothing runs a
   reduced backtest, so every candidate costs a full one.

## 7. Verdict

**Discovery: good.** 1.3 configurations per construction, 11 distinct
mechanisms in 11 hypotheses, new families composed at runtime. It is not
mutating parameters and calling it research.

**Learning: poor.** One follow-up from eight failures, with a root cause now
fixed but unmeasurable in this environment.

**Efficiency: mediocre and honestly reported.** Two-thirds of cycles refused,
all of them cheaply, all of them counted and attributed.

**The claim I will not make:** that the fixes in this branch improve measured
effectiveness. They fix defects that are real and demonstrated by unit test;
the campaign measurement cannot see them, and saying otherwise would be exactly
the kind of unearned claim this report exists to avoid.
