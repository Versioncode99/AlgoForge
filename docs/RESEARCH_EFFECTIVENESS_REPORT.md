# Is AlgoForge discovering genuinely new strategy constructions?

**Yes — and the ceiling that stopped it is gone, provably.** The evidence is a
controlled comparison through the real engine, not a template count.

Everything below is reproducible:

```
uv run python scripts/measure_vocabulary.py --cycles 200
uv run python scripts/campaign_comparison.py --cycles 320 --bars 30000
```

---

## The question, stated precisely

The previous phase's finding was not "the engine is bad at research". It was
that the engine had **run out of things to propose**, and that the novelty gate
was correctly refusing the restatements it produced: 78 of 81 refusals were
`SAME_CONSTRUCTION`. The gate was working. The vocabulary underneath it was
exhausted.

So the question is not "does it produce more experiments?" — a parameter sweep
produces unlimited experiments. It is:

> Can the engine now construct signals that are *structurally* different from
> anything it could construct before, and does a bounded campaign actually
> reach them?

Two separate claims, and both are measured separately below.

---

## 1. The reachable vocabulary

Measured by `scripts/measure_vocabulary.py`, which enumerates rather than
estimates.

| | Before | After |
| --- | ---: | ---: |
| Feature primitives | 21 | **47** |
| — observations (computed from bars) | 21 | 35 |
| — transformations (computed from a feature's own history) | 0 | 12 |
| Formal mechanisms | 0 | **12** |
| Signal shapes | — | 5 |
| Regime gates | 0 | 183 |
| **Distinct entry signatures / triggers** | **10** | **1,113** |
| **Reachable structural signatures** | **10** | **408,471** |

The "before" column for entry signatures is not an estimate. It is the count of
written archetypes, because `compose()` took an archetype whose entry condition
was a **constant**: direction, session gate and exit style varied over it and
the signal never did. Ten archetypes meant ten entry signatures, for ever.

### Is the larger number real, or padding?

Three checks, all in `tests/research/test_adversarial_vocabulary.py`:

* **Every shape and every observable is reachable** by some construction. A slot
  nothing can fill would inflate the count without enlarging the vocabulary.
* **600 consecutive draws produced 600 distinct structural signatures**, none
  repeating.
* **The signature ignores every number.** Two constructions differing only in a
  lookback or a threshold hash identically, so a parameter sweep cannot be
  counted as discovery. Renaming cannot either: the identity is a content hash
  of the slots that were filled, and nothing in the naming reaches it.

And a fourth, in `scripts/measure_vocabulary.py`: of 200 drawn constructions,
**200 composed, 200 compiled, 200 passed the static guard**, 191 were
backtestable within the warm-up budget and 138 took trades. A vocabulary of
signals that do not run is a larger number, not a larger vocabulary.

---

## 2. What a bounded campaign actually does with it

Three arms through the **real** director, the real novelty gate, the real
template store with its static guard and smoke test, and the real backtest.
320 cycles each, 30,000 synthetic bars.

| | A — grammar off | B — expanded | C — throughput |
| --- | ---: | ---: | ---: |
| **Unique structural constructions** | **10** | **51** | **53** |
| — of which assembled | 0 | 41 | 43 |
| Hypotheses admitted | 10 | 51 | 53 |
| Distinct mechanisms | 10 | 20 | 20 |
| Experiments run | 80 | 121 | 116 |
| Frontier items reached | 10 | 51 | 53 |
| **Cycles with nothing to propose** | **240** | **0** | **0** |
| Seconds | 20.9 | 50.8 | 51.4 |

Arm A is the engine as it was: the ten written archetypes and nothing else.

**It stops at exactly ten.** Not approximately ten — ten, which is the number of
written archetypes. It was ten at 120 cycles and ten at 320 cycles. The extra
200 cycles produced no new construction and **240 of the 320 cycles had nothing
to propose at all**. Its 80 experiments are 80 parameter draws over 10
templates, which is the parameter search the previous phase named.

Arms B and C reach 51 and 53 distinct constructions and are still climbing; the
mechanism count doubles from 10 to 20. Neither arm had a single cycle with
nothing to do.

### An earlier measurement of the same thing

Before the compute-efficiency fixes below, the same comparison at 120 cycles
reproduced the previous phase's finding almost exactly:

| | A | B | C |
| --- | ---: | ---: | ---: |
| Unique constructions | 10 | 25 | 25 |
| Duplicates rejected | 85 | 71 | 60 |
| `SAME_CONSTRUCTION` share of refusals | 84% | 92% | 94% |
| Novelty rate | 0.29 | 0.41 | 0.50 |

84% of arm A's refusals were `SAME_CONSTRUCTION`, against the 96% the previous
phase measured. That is the failure mode, reproduced on demand.

---

## 3. The bottleneck that was found on the way, and fixed

The 320-cycle run exposed a second bottleneck that had nothing to do with the
vocabulary, and it is worth stating because it was invisible until the first one
was removed.

**The director built the template before it checked the claim.** A cycle
composed a definition, rendered it to Python, ran the static guard, registered
it in the template store — which runs a smoke test over synthetic bars — and
*then* asked the novelty gate whether the claim was new. When the answer was no,
it deleted the template again.

Measured: **220 of 320 cycles** in one campaign did all of that work and threw
it away, for a verdict that costs nothing and could have come first.

Reordering it — assess, then build — took arm B from 29 unique constructions to
**51** over the same 320 cycles, a 76% increase, at the same wall clock.

Two smaller wastes were found the same way and are fixed:

* A written archetype the campaign had already built was still being drawn as a
  fresh proposal, so the ten seeds came round for ever. They are now filtered by
  the same structural signature the assembled half uses.
* A "rate" regime gate declared a tunable parameter whose low equalled its high.
  The template store refuses that, so four cycles per 120 were spent composing,
  rendering and being rejected for a parameter that should never have existed.
  A rate is compared against zero and has no parameter.

---

## 4. What is still bounded, and by what

**This is the honest part.** The vocabulary reaches 408,471 structural
signatures and a 320-cycle campaign reaches 51. The gap is not the vocabulary.

### The binding constraint is now the novelty gate's use of prose

The gate compares hypothesis *text* and *mechanism text*. Two constructions
testing the same mechanism share a mechanism string exactly (similarity 1.00,
because there are twelve mechanisms), so the discrimination has to come from the
statement. Generated statements are assembled from a template, so a large share
of their characters is shared boilerplate.

Measured before this phase's fix: statements of structurally different
constructions scored 89–93% similar, and the gate refused them as
`PARAMETER` — "different numbers against a claim already on the frontier" —
when the numbers were not what differed.

The statement now leads with the construction rather than the mechanism, which
helps and does not close the gap: "a break of the rolling high" and "a break of
the opening range high", both under liquidity removal, still score above the
duplicate threshold. That refusal is **defensible** — they are close research —
but it is made on the wording rather than on the structure.

**The fix that would close it, and why it is not in this phase.** The gate has a
`STRUCTURAL` path that compares feature sets exactly, and it is never reached
for a fresh proposal: the corpus is built from hypothesis records, and a
hypothesis record does not store the features of the construction that
implemented it. Giving `Hypothesis` a feature column would let the gate band
these as `SAME_MECHANISM` — a different construction of the same explanation,
which is what they are, and which the admission floor already permits — instead
of as `PARAMETER`. That is a schema change to a durable store and it is stated
here as the next bottleneck rather than attempted at the end of a phase.

### Two smaller bounds, both deliberate

* **Allocation, not vocabulary, decides how often a new construction is
  proposed.** Most cycles go to refinement, advancement and robustness by
  design. A campaign that only ever proposed new constructions would never
  finish measuring one.
* **A warm-up budget of 800 bars** refuses constructions that need more history
  than a validation partition can hold. This excludes part of the reachable
  space — deliberately, because those cycles came back `BLOCKED` having measured
  nothing.

---

## 5. What this does not claim

* **No edge was found, and none was looked for.** Every measurement here is on
  the seeded, deliberately edge-free synthetic series, which cannot clear the
  judge's G0 data gate. No candidate in any arm was promoted and none could be.
  A run here reporting a promotion would be evidence of a bug.
* **A larger vocabulary is not a better one.** 408,471 reachable signatures is a
  ceiling on what could be explored, not a claim that all of it is worth
  exploring. Most of it will be wrong; the point is that the engine can now be
  wrong in new ways rather than in the same ten.
* **Novelty detection was not weakened.** `DUPLICATE_STATEMENT`,
  `DUPLICATE_MECHANISM`, `FAMILY_DISTANCE` and `STRUCTURAL_FEATURE_CHANGE` are
  unchanged, `REFUSED_BY_DEFAULT` is unchanged, and the adversarial suite
  asserts that a padded restatement is still refused.

---

## Verdict

> **Can AlgoForge discover something that did not exist in its original
> construction vocabulary?**

Yes. 41 of arm B's 51 constructions were assembled from primitives rather than
selected from the written set, and every one of them carries a structural
signature that did not exist before this phase. The arm without the grammar
produced ten and then had nothing to do for 240 consecutive cycles.

The remaining limit is a text-similarity gate reading generated prose, and it is
named above rather than worked around.
