# AlgoForge deep bug audit — Opus 5

Branch `main`, starting at `17f4d22`.
Date: 2026-09-08.
Method: adversarial probes first, source reading second. Every finding below was
reproduced by running code before it was fixed.

The brief for this pass was *assume the system is still wrong*. The previous
pass fixed five gates that could not fail; this one asks whether the fixes
themselves are sound, and whether the estimators behave on input nobody chose.

Two of the six confirmed bugs are in code written during the previous pass. That
is the expected outcome of actually attacking your own work rather than
re-reading it.

---

## Summary

| # | Finding | Class | Severity | State |
|---|---|---|---|---|
| 1 | Conformance evidence survives the suite being gutted | scientific validity | **High** | Fixed |
| 2 | A moved hypothesis can be laundered by a throwaway run | scientific validity | **High** | Fixed |
| 3 | Non-finite trial Sharpes produce a `NaN` verdict and invalid JSON | correctness + API contract | **High** | Fixed |
| 4 | A search whose trials all score the same silently disables deflation | scientific validity | **High** | Fixed |
| 5 | "No drawdown" is reported as bad risk | reporting | Medium | Fixed |
| 6 | A finite series can overflow the cumulative curve | correctness | Medium | Fixed |

---

## 1. Conformance evidence survives the suite being gutted

**Class:** scientific validity. **Severity:** High. **Introduced:** previous pass.

**What should happen.** G2 asserts that a strategy's own conformance suite
passes, including its lookahead trap. If the suite changes, the stored verdict
is about a different suite and must not be reused.

**What actually happened.** `conformance_verdict` keyed the stored report on
`code_hash` alone. The suite lives in a *separate file*, so it can be replaced
wholesale — the lookahead trap deleted outright — without the strategy's code
hash moving at all.

**Reproduction.** Run the suite honestly, then overwrite `test_strategy.py` with
`def test_nothing(): assert True`:

```
honest run           : passed=True  3_PASSED
after gutting suite  : conformance_verdict=True
code_hash unchanged  : True
>>> G2 still reads PASS from a suite that no longer exists
```

**Why.** I keyed the evidence to one half of the pair it is actually about. The
report is a claim about *this code, checked by these tests*; only one of the two
was bound.

**Fix.** `test_hash` is stored on the report and checked alongside `code_hash`.
`ensure_conformance` recomputes the suite hash, so editing the test file
re-measures rather than reusing a verdict about a different suite. Gutting it
now yields `None` — absent evidence, INCONCLUSIVE — rather than `True`.

**Test.** `test_gutting_the_suite_invalidates_the_evidence`,
`test_ensure_re_runs_when_only_the_suite_changed`.

**Could the fix break anything?** It makes G2 INCONCLUSIVE for any strategy
whose suite was edited since its last report. That is the intended meaning, and
`ensure_conformance` re-runs on the next judge, so it self-heals.

---

## 2. A moved hypothesis can be laundered by a throwaway run

**Class:** scientific validity. **Severity:** High. **Introduced:** previous pass.

**What should happen.** G1 asserts the claim frozen *before this run* still
describes what is being judged. Rewriting a hypothesis after seeing a result
must be detectable.

**What actually happened.** G1 read the store: *"was any matching claim ever
frozen for this strategy?"*. `claim_holds` returned `True` if **any** stored row
matched the re-derived claim, and `_preregister` appends a claim on every
backtest — including a synthetic one, which costs nothing.

So: run honestly, dislike the result, rewrite the hypothesis, start any
throwaway backtest to freeze the new claim, and the new claim then validated the
**original** artifact.

**Reproduction.**

```
claim frozen, holds  : True
after rewriting      : False   <- correctly caught
after a throwaway run: True    <- laundered
```

**Why.** Nothing bound a claim to the run it preceded. A per-strategy lookup
cannot answer a per-run question, and no amount of checking the store harder
fixes that — the information is not in it.

**Fix.** `BacktestResult` carries `preregistration_hash`, set at run time. G1 now
re-derives the claim from the *current* spec and the parameters the artifact
records, and compares against the hash the run itself carries
(`claim_holds_for_run`). The old artifact keeps its old hash, so re-freezing
cannot reach it. The store remains as the audit trail — *which claims were ever
registered* — which is a genuinely different question.

The engine was never vulnerable: it binds the hash to the *experiment row*
created before the backtest. Its artifacts now carry the hash as well, so the
dossier can read it.

**Test.** `test_a_moved_claim_cannot_be_laundered_by_a_throwaway_run`,
`test_the_run_carries_the_claim_it_executed_under`,
`test_moving_the_parameters_is_caught_by_the_binding_too`.

**Could the fix break anything?** Every artifact written before the field existed
reports `None` — absent, INCONCLUSIVE. Correct: those runs genuinely recorded no
claim.

---

## 3. Non-finite trial Sharpes produce a `NaN` verdict and invalid JSON

**Class:** correctness, and an API contract bug. **Severity:** High.

**What should happen.** `_clean` refuses non-finite *returns*. The same care
should apply to the trial Sharpes.

**What actually happened.** It did not. A NaN or infinity in `trial_sharpes` went
straight through `np.var` into the Deflated Sharpe:

```
sharpes containing NaN   DSR=nan V[SR]=nan hurdle=nan
sharpes containing inf   DSR=nan V[SR]=nan hurdle=nan
```

Two consequences. `nan >= 0.95` is `False`, so G5 failed — for the wrong reason,
reported as a measurement. And `metrics["deflated_sharpe"] = nan` serialises to a
bare `NaN` token:

```
json.dumps emitted NaN token: True
strict parse FAILS: non-JSON constant: NaN
```

`JSON.parse` in the browser rejects that. The interface would fail to load the
verdict, with no indication why.

**Fix.** `_trial_spread` validates the spread before use and distinguishes three
failures that mean different things: absent (`V[SR]_ASSUMED_NOT_MEASURED`),
non-finite (`TRIAL_SHARPES_NON_FINITE`), and degenerate (finding 4). Non-finite
falls back to the documented assumption and G5 reports INCONCLUSIVE.

**Test.** `test_non_finite_trial_sharpes_do_not_reach_the_verdict`,
`test_no_metric_is_ever_non_finite`,
`test_the_payload_survives_a_strict_json_parser`.

---

## 4. A search whose trials all score the same silently disables deflation

**Class:** scientific validity. **Severity:** High. **The worst of the six.**

**What should happen.** The Deflated Sharpe raises the bar in proportion to how
many things were tried. A thousand-trial search must clear a higher hurdle than
a single hypothesis.

**What actually happened.** The hurdle is `sd(SR) × Gumbel(N)`. When every
configuration scores the same, `np.var(ddof=1) == 0`, and `expected_max_sharpe`
returns `0.0` because `deviation == 0.0`. The hurdle collapses to zero and the
DSR silently becomes a plain PSR — **no deflation at all**, for a search that may
have tried a thousand things.

```
all identical sharpes    DSR=0.9944  V[SR]=0  hurdle=0
```

`MINIMUM_TRIAL_CONFIGURATIONS = 8` does not catch it: forty identical Sharpes
clears every count check.

**Why this is the most dangerous of the six.** It fails *permissively*, and the
output is indistinguishable from a correctly deflated result. Every other
finding here fails closed or fails loudly.

**Is it reachable in practice?** Yes, and not exotically. A grid whose parameters
do not bite — a threshold above every observation, a filter that never triggers —
produces identical results at every setting. That is a common outcome of an
automated search, not a contrived one.

**Fix.** `DeflatedSharpe` gains `spread_problem` / `spread_is_measured`. The
spread is degenerate when the range across trials is at or below `1e-12` relative
to their magnitude — relative rather than absolute, because Sharpes are
scale-free but not magnitude-free. `_deflation_measurable` now requires a
measured spread as well as an adequate count, so G5 reports INCONCLUSIVE with
`TRIAL_SHARPES_HAVE_NO_SPREAD`. `credible` also requires it, so no caller can
read a zero-hurdle DSR as credible.

**Test.** `test_a_search_whose_trials_all_scored_the_same_cannot_deflate`,
`test_a_spread_that_is_only_floating_point_noise_is_still_no_spread`,
`test_a_real_spread_still_deflates`.

---

## 5. "No drawdown" is reported as bad risk

**Class:** reporting. **Severity:** Medium.

`calmar_ratio` returns `0.0` when the drawdown is zero — the same number a
genuinely terrible strategy gets. G8's rule reads *"Calmar >= 0.50 (net pnl at
least half the worst drawdown)"*, so a run that never drew down was reported as
failing on risk with an observed value of `0.0`. A reader would conclude poor
risk-adjusted return about a run that had no drawdown to divide by.

The verdict was defensible; the *statement* was false.

**Fix.** G8 is an evidence gate. Zero drawdown reports INCONCLUSIVE with
`NO_DRAWDOWN_TO_MEASURE`. Risk is unmeasured there, not bad — and the pass is
still withheld, so nothing is weakened.

**Test.** `test_a_run_with_no_drawdown_reports_risk_as_unmeasured`,
`test_a_real_drawdown_is_still_judged`,
`test_a_losing_run_with_a_real_drawdown_still_fails_risk`.

---

## 6. A finite series can overflow the cumulative curve

**Class:** correctness. **Severity:** Medium.

`evaluate` refused non-finite P&L but not P&L that *becomes* non-finite. Every
drawdown-derived metric runs on `np.cumsum`, and forty individually finite
values of `1e308` overflow it, producing a NaN drawdown, a NaN Calmar, and
another bare `NaN` in the payload.

**Fix.** The guard checks the cumulative curve as well as the values, under
`np.errstate` so the check does not emit the warning it exists to handle.
Genuinely large but summable magnitudes still judge normally.

**Test.** `test_a_series_that_overflows_when_summed_is_refused`,
`test_extreme_but_summable_magnitudes_are_still_judged`.

---

## Attacks that did **not** succeed

Recorded because a clean result is evidence too.

- **NaN / infinity / empty P&L** — refused by `_clean` at the entry point.
- **Degenerate series reaching PASS** — constant, all-zero, alternating,
  denormal and near-constant series were pushed through the ladder with every
  non-statistical gate handed a pass. None reached PASS.
- **A single enormous win carrying the verdict** — 59 tiny losses and one
  million-dollar win fails G6 on the permutation test.
- **Zero variance producing an infinite Sharpe** — `per_period_sharpe` returns
  `0.0` when the deviation is zero rather than dividing.
- **PBO on a 2-column matrix** — already refused; the adequacy floor holds.
- **The engine's preregistration path** — bound to the experiment row before the
  backtest, so finding 2 never applied to it.

---

## Method note

Findings 1 and 2 were found by writing the attack first and running it, not by
reading the code. Both modules had been read carefully during the pass that
introduced them, and both read as correct. The probe took twenty lines and found
them in one run.

Finding 4 was found by feeding the estimator input nobody would choose — forty
identical Sharpes — and reading the hurdle rather than the verdict.
