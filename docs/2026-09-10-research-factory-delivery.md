# The autonomous research factory — what was built

Date: 2026-09-10
Branch: `claude/cloud-environment-overview-o7an7t`, from `main` @ `c0c6247`
Companion: [the architecture map and plan](2026-09-10-research-factory-architecture.md)

Status: implemented and locally verified. Nothing here claims a strategy has an
edge, that a discovery has been made, or that the engine is now profitable. Every
claim below is about the *behaviour of the research machinery* and is backed by a
test.

---

## 1. The diagnosis was right, and the cause was one line

`AutonomousEngine._cycle` began with:

```python
template_key = rng.choice(sorted(TEMPLATES))
params = {p.name: uniform draw from p.low..p.high for p in template.parameters}
```

`TEMPLATES` was a fixed dict the engine only ever read. Family and template
creation existed as operator actions the engine never called. A hypothesis was a
*string field on a template*, so two hundred parameter draws produced two hundred
experiments carrying one claim and nothing could tell that they were one question
asked two hundred times. Failures pruned but never generated. There was no budget
model, so 100% of compute went to category A.

Everything downstream of that line was excellent and is untouched.

## 2. What changed

One line of the execution path:

```python
research = self._directed_candidate(rng, worker, policy)
```

The director returns the same two things the engine always needed — a template
key and a parameter set — plus the research context that did not previously
exist. With no campaign attached it returns `None` and the original draw runs
unchanged, so stopping a campaign does not stop the engine.

## 3. New modules

| Module | What it holds |
|---|---|
| `forge/research/frontier.py` | Nine epistemic states, provenance, a transition log. `UNTESTED ≠ FAILED`. No veto. |
| `forge/research/hypotheses.py` | Durable graph, typed edges, ancestry and descendants, `distinct_mechanisms`. |
| `forge/research/novelty.py` | Deterministic similarity over claim, mechanism and signal structure. Five categories. |
| `forge/research/allocation.py` | 35/25/20/10/10, drawn per cycle, adapting within a bounded drift. |
| `forge/research/information.py` | novelty × evidence gap × leverage ÷ cost, with per-question decay. |
| `forge/research/followup.py` | Failures and conditional results → falsifiable questions. |
| `forge/research/synthesis.py` | Ten signal archetypes → `StrategyDefinition`. Data, never code. |
| `forge/research/campaign.py` | Objective, budgets, enforced stopping criteria, progress counters. |
| `forge/research/promotion.py` | Explicit validation queue. Three outcomes, and the middle one is real. |
| `forge/research/journal.py` | Typed per-campaign event stream. |
| `forge/research/literature.py` | arXiv + Crossref with strict provenance and verbatim claim spans. |
| `forge_api/director.py` | The single decision point. |
| `forge_api/campaigns.py` | The HTTP surface. |

## 4. Evidence it does research rather than parameter search

A twenty-cycle campaign on the synthetic dataset, run through the real engine:

```
experiments              15
hypotheses                9
mechanisms                9
families_created          4
templates_created        13
duplicates_rejected       5
followups_generated       1
spend  {REFINE_PARAMETERS: 3, EXPLORE_HYPOTHESIS: 3, ADVANCE_PROMISING: 3,
        ROBUSTNESS: 4, DISCOVER_FAMILY: 2}

families created:
  discovered_horizon_divergence, discovered_volatility_expansion,
  discovered_volume_shock, discovered_vwap_deviation
```

Fifteen experiments across **nine distinct mechanisms**, with four families and
thirteen templates composed and five proposals refused as duplicates. Before this
change the same run would have been fifteen parameter draws across at most a
handful of shipped templates and exactly zero new questions.

Every failure in that run was `G0 (Data integrity)` — the synthetic dataset
cannot clear the data gate, which is correct and is asserted rather than hidden.

## 5. The safety story, unchanged

Autonomous structural discovery reuses machinery that already existed:

```
StrategyDefinition (data)
  → validate_definition()   well-formedness, sufficient warmup
  → to_python()             renders the module
  → assert_safe()           no fs, net, subprocess, dynamic exec
  → verify_python()         the rendered Python reproduces the IR's own ledger
  → TemplateStore.create()  parameter grid checks, guard, synthetic smoke test
  → pilot                   before it counts for anything
```

The director's authority stops at choosing from a closed vocabulary of ten
archetypes. It never extends to writing source. `test_synthesis.py` asserts all
of the above for every archetype at several seeds, including that the generated
Python reproduces the definition's ledger bar for bar.

## 6. Scientific integrity

Explicitly held, and tested:

- **G0–G13 untouched.** No gate was weakened, reordered or reinterpreted.
- **Nothing in the research layer reaches `JudgeInput`.**
- **`INCONCLUSIVE` is not a failure** — `outcome_from_verdict` maps unmeasured
  gates to `INCONCLUSIVE`, and the frontier refuses to record it as `FAILED`.
- **A finding is never evidence.** `ResearchKnowledge` already enforced this;
  derived hypotheses inherit it and enter as `UNTESTED`.
- **Trial inflation cannot be laundered.** Every generated candidate still
  increments `Experiments.count`, which is what G5's deflation reads.
- **Synthetic data cannot produce a discovery.** The promotion queue refuses a
  synthetic candidate and names the synthetic data as the reason.
- **No invented citations.** With no network, `literature.retrieve` returns
  nothing and records the failure. There is no construct-a-plausible-paper path.
- **No parallel registries.** The existing `FamilyRegistry`, `TemplateStore`,
  action registry and `WorkspaceStore` are the ones used.

## 7. Workspaces

`restore_session` (last open, then default, then nothing) · append-only version
history with actor attribution · export/import as a portable document · collapse
and reorder as first-class operations · every verb an action in the registry
before it is a route. `delete_workspace` remains held for a person, even from the
agent.

## 8. Bugs found and fixed along the way

1. **The IR had no volume baseline.** A liquidity signal could only compare
   volume against a *price* average — a category error that silently never fires.
   `volume_sma` added to the feature vocabulary with an implementation, an
   exporter mapping and tests; two archetypes were unreachable without it.
2. **Novelty compared against the wrong neighbour.** The mechanism check read the
   combined-nearest subject rather than the closest *mechanism*, so a proposal
   restating family X while worded closer to family Y passed as a new mechanism.
3. **Jaccard punished embellishment.** "The same mechanism, plus a clause" scored
   as new. `containment` added alongside, and `overlap` takes the stronger
   reading.
4. **`PromotionQueue.claim` returned a stale row** whose `state` still said
   `QUEUED` after the update marked it `RUNNING`.
5. **A latent circular import.** `forge.research.__init__` eagerly imported
   `synthesis`, which imports the strategy IR, which imports
   `forge.strategy.models`, which imports `forge.research.models`. It worked or
   exploded depending on which package the process touched first.
6. **Two archetypes could never fire** at their declared defaults — a compression
   threshold below the reachable range, and the volume comparison above.
7. **The exploration bucket stalled the moment the frontier had anything open.**
   Picking up an existing frontier item ran the "is this claim new?" gate on it
   and refused it as a duplicate of its own hypothesis, so every attempt to
   *answer* an open question produced a refusal instead of an experiment.
   Testing an admitted question is a second *construction*, not a new claim, and
   is now recorded as `STRUCTURAL` against the existing hypothesis.
8. **`test_no_shipped_template_can_only_take_one_side` read the whole runtime
   catalogue.** The catalogue is open by design — an operator or the director
   may register into it — so the test failed on templates this repository does
   not ship and makes no promise about. `SHIPPED_TEMPLATE_KEYS` is captured at
   import, before anything can register, and the two symmetry tests now honour
   their own names.
9. **Orphaned generated templates.** A template was registered to prove its claim
   was implementable, and left behind when the claim was then refused as a
   duplicate.

## 9. A note on test isolation

`forge.strategy.TEMPLATES` is process-wide mutable state that two things write
into at run time. `tests/conftest.py` now snapshots and restores it around every
test, alongside the workspace and storage-pointer guards already there — a
generated template outliving its test made a full run fail in a different file
from the one that caused it, which is the worst kind of failure to chase.

Two of the bugs above were found only by running the *whole* suite: they were
invisible per-file and per-directory. Worth remembering.

## 10. Tests

New: `tests/research/test_frontier.py`, `test_hypotheses.py`, `test_novelty.py`,
`test_allocation.py`, `test_followup.py`, `test_synthesis.py`, `test_campaign.py`,
`test_promotion.py`; `tests/api/test_director.py`, `test_research_factory.py`,
`test_workspace_ai.py`; `tests/workstation/test_workspace_lifecycle.py`;
`apps/web/src/views/campaign.test.tsx`.

`tests/api/test_research_factory.py` is the commercial-viability test: it drives
the real engine through a bounded campaign and asserts the behaviour of the
factory, never that a particular strategy wins.

## 11. What is not done

Stated plainly rather than left to be discovered:

- **No real-data campaign has been run.** The vendor archives are not in this
  environment, so every run here is on the synthetic fixture. The 16-year NQ
  progression the brief describes is untested against real bars.
- **Web research is untested against the live indexes.** The retrieval code has
  unit-level coverage and strict provenance, but no run in this environment
  reached arXiv or Crossref.
- **The validation queue records inline runs rather than driving separate ones.**
  The engine validates inside the cycle — the parameter grid, the walk-forward
  and the CPCV paths all run there and the judge reads them — so the queue
  records what a candidate earned and what the judge then decided, including
  which gate settled it. What it does *not* yet do is schedule a validation of
  its own on a separate worker, which is what would let an expensive validation
  run be deferred and prioritised rather than paid for during the cycle that
  earned it.
- **Regime discovery** (improvement 7) is not implemented. `forge.analytics.regime`
  exists and the follow-up rules generate regime hypotheses, but nothing
  systematically searches for regimes.
- **The workspace optimizer** (improvement 9) is not implemented.
- **Manual UI QA is partial.** The build, the type check and the component tests
  pass; the campaign screen has not been driven against a long-running real
  campaign.
