# AutoResearch V2 — Implementation Report

Branch `feat/autoresearch-v2`, cut from `feat/openterminal-workstation-integration`
(`b6b5f8c`). Six commits at the time of writing.

---

## 1. The finding that reordered the work

**The brief's premise was inverted.** It asks that the sixteen-year archive not
be treated as a mandatory experiment window. AlgoForge never did that; it did
the opposite, and worse.

```python
# engine.py — once per run, shared by every worker and every experiment
self._loaded = self.market.load(config.dataset, limit=config.max_bars)
# market.py
window = frame.tail(limit) if limit and limit < len(frame) else frame
```

`max_bars` defaults to 250,000 ≈ **nine months** at ~347,760 one-minute bars a
year, always the most **recent** nine months. A day-of-week seasonality
hypothesis — the brief's own example of something needing ten to sixteen years —
got nine months, and nothing told anybody.

`Campaign.start_date` / `end_date` were accepted, validated, stored in their own
columns, returned by `as_dict`, copied by `duplicate` — and **read by nothing**.

The system the brief asks for is the right fix for the real problem too, so the
work barely changed. The justification did, and the audit was written against
measured behaviour rather than the assumed behaviour.

## 2. What was built

| | Where | Tests |
|---|---|---|
| `TimeScope`, nine selection methods, leakage checks, selection exposure | `research/timescope.py` | 26 |
| `ResearchPlan`, deterministic plan gate, `PilotDesign`, promotion rule | `research/plan.py` | 28 |
| Scope-aware loading, measured reservoir, bar-count sufficiency | `forge_api/market.py` | 10 |
| Per-campaign windows in the engine, cached by fingerprint | `forge_api/engine.py` | 10 |
| Four registry verbs (154 actions total) | `forge_api/actions.py` | 16 |
| End-to-end lifecycle + restart | `tests/research/test_research_lifecycle.py` | 2 |

**92 new tests.**

### The integration worth reading twice

`Preregistration` already froze the claim, content-hashed it, and **G1 already
re-derives that hash at judge time and fails `CLAIM_MOVED`.** Putting the
scope's fingerprint inside that payload makes "ran two years, disliked the
answer, reported the five-year number" a G1 failure — with **no second freeze,
no new gate, and no change to the ladder**.

Folded in **conditionally**, so every claim frozen before scopes existed hashes
identically. Unconditionally would have reported `CLAIM_MOVED` for every
strategy in the library, invalidating evidence nobody had touched.

## 3. Defects found and fixed

| # | Severity | Defect |
|---|---|---|
| 1 | **P0** | **Scoped bars judged against the default window.** Introduced by this work. `ResearchPartitions` holds bar *lists*; `_cycle` fell through to `self._partitions`, cut from the default window. Fixed by threading partitions with the bars. |
| 2 | P1 | **`load_range` ignored `pad_bars` on non-imported datasets.** A strategy given no warm-up computes its first indicators on insufficient history and reports nothing unusual. |
| 3 | P1 | **`Campaign.start_date`/`end_date` read by nothing.** Now drive the window. |
| 4 | P2 | **Registry verbs did not compose.** `propose_time_scope`'s payload was refused by `review_research_plan` for carrying its own derived keys. |

### On defect 1

It is the exact failure this feature exists to prevent, reintroduced by the
feature, and **it passed all eight tests written for the feature** because the
synthetic dataset is not `is_real` and the partitioned path never ran.

The regression test then needed strengthening twice. The first version called
`_bars_for` once — the cache-*miss* branch — and re-introducing the leak in the
cache-*hit* branch left it passing. It now loads twice and was confirmed to fail
with the leak in place and pass without it.

Two lessons recorded in the architecture doc: a fixture that cannot reach a code
path cannot test it, and a cache has two return paths where a test usually
exercises one.

## 4. Design decisions worth defending

**`TimeScope` carries no minimum window length.** The first draft had twenty
days. That is wrong: twenty days of one-minute bars is ~6,000 observations and
is plenty; twenty days of daily bars is twenty and is useless. Sufficiency is a
bar count, the module's own docstring says it does not read data, and assuming a
bar rate anyway would be the assumption it claims not to make. The count is
checked where it can be counted.

**`INCONCLUSIVE` promotes a pilot; `NO_SIGNAL` does not.** A pilot is small by
construction, so "could not tell" is the expected answer for a real effect on a
short window. Treating it as a refusal rejects exactly the hypotheses a pilot is
too small to see.

**`IMPLEMENTATION_FAILURE` is explicitly not a negative result.** The strategy
did not run, so the pilot says nothing whatever about the hypothesis. Reading a
broken strategy as a dead idea is how real ideas get discarded.

**`PLAN_BLOCKED_DATA` is separate from `PLAN_REJECTED`.** Research that could
never work is discarded; research whose data is absent is kept, because it
becomes runnable when the data arrives. Collapsing them loses the question.

**A degraded window is never silent.** A window that cannot be loaded falls back
to the run's and logs it; too small to partition is logged as a fact about the
window; an incoherent range falls back because a misconfiguration is not a
research emergency; a range reaching before the archive is clamped with both
ends kept so the clamp stays inspectable.

## 5. What is **not** built

Stated plainly, because the brief asks that nothing be claimed unverified.

- **No plan store.** Plans are constructed, gated and preregistered; they are
  not persisted in their own table. Lineage lives in the hypothesis graph and
  the journal.
- **The engine does not build plans.** `_cycle` still proposes a `StrategySpec`
  through the director. The plan and gate are reachable from the registry and
  tested end to end; the autonomous loop has not been rewritten to route
  through them.
- **Pilots are not executed.** The design, outcomes and promotion rule exist and
  are tested; nothing runs a reduced backtest. The engine *does* already screen
  on the development partition before touching validation — a cheap gate before
  an expensive path, but not a *temporal* pilot.
- **No plan critic.** `agents/debate.py` critiques a verdict; nothing critiques a
  plan before compute is spent.
- **No multi-model idea forge.** Per-role model routing exists (9 roles);
  independent cross-review does not.
- **The freshness envelope is still unconsumed** on the market-data read path —
  carried over from the previous branch and unchanged here.

## 6. Honest limits on the evidence

**There is no real archive in this environment.** `data/market/databento/` is
empty. Every claim here is about the *mechanism*, tested on synthetic bars and
explicit date arithmetic. Nothing in this report is a measured result on sixteen
years of NQ, and the tests are written so that they could not be mistaken for
one.

That also means the `is_real` arm of `_cycle` — the partitioned backtest path
where defect 1 lived — is not exercised by any test in this repository on real
data. The regression test drives `_bars_for` directly and asserts on the
partitions it returns, which is the strongest check available without an
archive.

## 7. Verification

- `ruff check .` clean
- `mypy` clean, 189 source files
- Backend suite: run on the current head, result in the progress document
- G0–G13 untouched: `git diff origin/main...HEAD -- packages/forge/judge/` is empty

## 8. Next

1. Route `_cycle` through `ResearchPlan` so the autonomous loop uses the gate it
   now has.
2. Execute pilots on the pilot scope, and promote on the existing rule.
3. Persist plans, so a plan's lineage is a record rather than a reconstruction.
4. The plan critic, before compute rather than after.
