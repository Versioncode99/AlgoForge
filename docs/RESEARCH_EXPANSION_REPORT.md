# Research expansion — what changed, and what it cost

The phase's brief was to enlarge AlgoForge's research vocabulary, enable
external research, expand and sharpen the agent architecture, give the operator
real control over models and budget, make the whole thing faster, and prove all
of it with measurements.

This is the account. Two companion documents carry the evidence in detail:
[`RESEARCH_EFFECTIVENESS_REPORT.md`](RESEARCH_EFFECTIVENESS_REPORT.md) answers
whether the engine actually discovers new constructions, and
[`PERFORMANCE_ENGINEERING_REPORT.md`](PERFORMANCE_ENGINEERING_REPORT.md) carries
the before-and-after latency work. [`RESEARCH_EXPANSION_PROGRESS.md`](RESEARCH_EXPANSION_PROGRESS.md)
is the running log, including the defects found on the way.

---

## 1. Construction vocabulary before

Ten signal archetypes, 21 IR feature kinds, 12 shipped Python templates.

The number that mattered was not any of those. `forge.research.synthesis.compose()`
took an `Archetype` whose `long_condition` and `short_condition` were
**constants**. Everything the composer varied — direction, session gate, exit
style — left the entry condition untouched. So:

* 600 enumerable definition hashes,
* and **10 distinct entry signatures**, permanently.

The novelty gate's `STRUCTURAL` check requires at least 34% of the feature set to
change. With ten fixed feature sets that could only be satisfied by jumping
between archetypes, which is why a 120-cycle campaign exhausted the space and 78
of 81 refusals were `SAME_CONSTRUCTION`. **The gate was right. The vocabulary had
run out.**

## 2. Construction vocabulary after

| | Before | After |
| --- | ---: | ---: |
| Feature primitives | 21 | **47** |
| Formal mechanisms | 0 | **12** |
| Signal shapes | — | 5 |
| Regime gates | 0 | 183 |
| Distinct triggers | **10** | **1,113** |
| Reachable structural signatures | **10** | **408,471** |

Counted by `scripts/measure_vocabulary.py`, not estimated.

## 3. Feature primitives

`packages/forge/strategy/primitives.py` splits the closed set in two.

**Observations** (35) are computed from the bars. New ones: `typical_price`,
`bar_range`, `true_range`, `gap`, `clv`, `signed_volume`, `upside_vol`,
`downside_vol`, `efficiency_ratio`, `variance_ratio`, `return_autocorr`,
`session_high`, `session_low`, `session_range_position`.

**Transformations** (12) are computed from *another declared feature's own
history*: `mean`, `stdev`, `zscore`, `percentile_rank`, `slope`, `accel`,
`change`, `pct_change`, `ewm`, `max_of`, `min_of`, `persistence`.

The second half is what multiplies rather than adds. A z-score of an ATR is not
an indicator anybody has to name and implement; it is the ATR observed against
the last N of itself, and it is the difference between a threshold fitted to one
chart and one that means the same thing on the next instrument.

Every primitive is causal, deterministic, and answers NaN rather than guessing
when it has insufficient history. `window_length` states exactly how much history
each one reads, so warm-up is derived rather than hoped for.

## 4. Mechanism primitives

`packages/forge/research/mechanisms.py`: twelve mechanisms, each carrying a
claim, a falsifiable prediction written against the observation the construction
actually made, the feature categories a signal must read to be testing it, what a
failure rules out and what it leaves open, a **stance** (continuation or
reversion) and a **reading** (which end of its own distribution the claim is
about).

The last two are not labels. Stance decides whether a trailing exit or a fixed
target is coherent with the claim — a fixed target caps exactly what a
continuation claim predicts, and then the result measures the exit. Reading is
what stops "quiet stretches cluster" being attached to a signal that only fires
when volatility is loud: the prediction would ask for a comparison across a split
the sample never straddles.

## 5. Research grammar

`packages/forge/research/grammar.py` assembles a signal instead of selecting one:

    OBSERVABLE → TRANSFORMATION → SHAPE → STANCE → REGIME GATE → SIGNAL

Most of the module is refusals, because size without constraint is an expression
generator rather than a vocabulary:

* **Units must agree.** A shape states the unit each slot needs. A
  scale-dependent reading cannot meet a scalar threshold. A standard deviation of
  price is measured in price and is still not a price that can be crossed, so it
  carries its own unit.
* **A trigger must be directional.** "Long when the ATR percentile is high, short
  when it is low" has a signal on every bar and makes no claim about direction —
  the most plausible broken strategy there is. Magnitude readings can gate a
  signal; they cannot be one.
* **A threshold's short leg is a real mirror**, stated against the unit's neutral
  point rather than by flipping the long leg's comparison, which would give a
  short that is true almost always.
* **A signal must be able to see its own mechanism**, in both category and
  reading.

An assembled construction is converted into a real `Archetype`, so `compose`, the
template store, the static guard, the smoke test, the novelty gate, the frontier
and the campaign record all run the code they already ran. **There is no second
pipeline** and therefore no second set of protections to keep in step.

The space is sampled, never enumerated. `DrawConstraints` carries what the
campaign is for, what it has already tried, and what the data supports; an
exhausted draw returns `None`, which the caller records as a finding rather than
papering over with a near-duplicate.

## 6. External research

On by default. The retrieval layer has no credential and no arbitrary-URL fetch:
it searches two fixed public indexes (arXiv q-fin, Crossref), follows no
redirect, caps the response size and the timeout, and records a failure as a
failure. There is no code path that invents a paper.

The operator can choose source categories, freshness and depth, and each one
reaches the retrieval rather than only the screen. **Depth** sets how many
results a query asks for (3 / 6 / 10). **Categories** select which indexes are
asked — `CATEGORY_INDEXES` maps preprints to arXiv and journals to Crossref, so
"preprints only" does not quietly query both. **Freshness** reweights by
publication year rather than filtering it, because a hard year cut-off on a
narrow query returns nothing and reads as "retrieval failed". Choosing *no*
source is kept as the deliberate instruction it is rather than read as "search
everything".

`tests/research/test_retrieval_policy.py` asserts this against a recording
transport: it checks which hosts were actually contacted for each category
choice, that the category map names only indexes that exist, and that freshness
changes the ordering without dropping older work.

## 7. From a claim to a question

`packages/forge/research/leads.py` is the step that was missing. Sources were
stored with their provenance and attached to frontier items as *references*, and
nothing read them: the claims were extracted, scored, and never turned into a
research question.

A lead is produced by **matching**, not generating. The claim's own words are
scored against the mechanism vocabulary and the observable vocabulary — both
closed sets — and a claim that matches nothing produces nothing, with the source
recorded as retrieved-and-unused. A model asked "what strategy does this paper
suggest?" answers something for a paper that suggests nothing, which is how a
citation ends up attached to a hypothesis it does not support.

Every lead carries the source, authors, publication date, URL, retrieval
timestamp, and the claim as a **verbatim span with the offsets it was copied
from**. Every lead is stamped `HYPOTHESIS_INPUT`. External research raises a
question; it never lowers the bar the experiment has to clear.

## 8. Agent architecture

Ten research roles, unchanged in number — and that is a finding rather than an
omission.

A `SYNTHESIS` role was added and removed. Its bucket preference came out
identical to `DISCOVERY`'s, which the engine's own `test_no_two_roles_prefer
_exactly_the_same_work` refused as one role with two names. The capability is
real and lives in `leads.py`; it runs deterministically and makes no model call,
so a role for it would be a settings entry that changes nothing.

Roles that would read well on a diagram and do nothing distinct here are absent
for stated reasons: a *campaign allocator* would duplicate
`forge.research.allocation`, which is deterministic and must stay that way; a
*supervisor* would duplicate the director, which cannot be allowed to be talked
out of a gate.

## 8a. Agent effectiveness

`packages/forge/research/effectiveness.py` joins the two halves that could not
answer the question separately. The registry counts experiments, findings and
errors; the skip ledger records every refusal with the agent that made it. An
agent that proposed forty things and had thirty-eight refused has an experiment
count that looks like work.

Three figures, deliberately different from each other:

* **Acceptance** — the share of proposals that became an experiment. The
  cheapest signal that a role is proposing into an exhausted corner.
* **Efficiency** — experiments per unit of compute. A fast agent producing
  refusals is not efficient however fast it is. Reported as *not measured* when
  compute is unmetered, rather than as 0.0, which reads as inefficient.
* **Effectiveness** — findings and experiments that reached a verdict. The one
  that matters and the one with the weakest instrument, so it is reported as a
  count of durable outcomes rather than as a score.

Three restraints, each asserted by a test. A role that has proposed nothing does
not score a perfect acceptance rate. Cycles with *nothing to propose* are not
counted against the agent, because an empty frontier is a fact about the
campaign. And nothing here allocates compute or retires an agent: a measurement
that changed what it measured would be unreadable, and an agent that could be
retired for a low score would make "propose nothing" the winning strategy.

Served on the control-centre payload and rendered in the Research Control Center.

## 9. Model routing

`apps/api/forge_api/model_routing.py`. The old table covered nine workflow roles
while the engine ran ten research agents of its own, so choosing "a model per
role" chose the model for none of the research. It now covers **21 roles across
both halves**, each with a model, a fallback and — where the system can run
without it — an enable switch.

Three modes. **Manual** uses the assigned model and nothing else, because a
substitution contaminates a comparison between two models. **Hybrid** falls
through assignment → role fallback → global fallback → default, naming each step.
**Smart** picks by what the job needs from the models the operator has allowed,
and never reaches outside that list.

Every selection returns a `Decision` carrying the model, where the choice came
from, and a sentence saying why. The settings screen shows, per role, which model
*would answer* — so an assignment the provider cannot serve reads as a
substitution rather than as the assignment. The old `model_for` substituted
silently.

## 10. Settings

The audit found three controls that did not do what they appeared to: an
editable base URL the endpoint accepted and discarded, a warning about a gateway
process removed two phases ago, and the role table above. All three are replaced,
and `tests/api/test_settings_surface.py` asserts through the real HTTP surface
that an assignment reaches the store and survives a read, that an unknown model
or role is refused with its name, and that the endpoint no longer takes a value
for the base URL at all.

Progressive disclosure: fallback and enable are behind one control, and the
research agents can be hidden when only the workflow matters.

A fourth control was built here and then removed: a per-role **critic** model.
It was stored, typed and rendered as a dropdown, and no code path ran a second
opinion through it. Two arguments decided it. The first is this phase's own
standard — a setting must affect behaviour, which is why the dead base-URL field
went. The second is that the capability already exists one level up:
`agent_reviewer` is a first-class role that "critiques lineage and evidence
quality rather than results", with its own model assignment, its own reasoning
demand tier and its own enable switch. A per-role critic field would have been a
second mechanism for a capability the architecture already carries, which is the
parallel system this phase's brief says to prove necessary before building. It
was not necessary, so the control is gone rather than half-wired.

## 11. Budget behaviour

Enforcement is a switch. With it **off**, no research ceiling is applied and the
screen says so plainly; the accounting keeps running, because knowing what was
spent is useful whether or not anything stops. With it **on**, ceilings are
configurable per dimension — experiments, model calls, backtests, wall clock,
external retrievals — and zero means "no ceiling on this dimension".

**System safety limits are not budget and do not move.** Concurrency caps, the
per-response token ceiling, request and retrieval timeouts, the agent ceiling,
the provider restriction and the permission function hold whatever the switch
says. They are served as data and rendered beside the switch, so what turning it
off does *not* turn off cannot drift from what the screen claims.

`tests/research/test_expansion_boundaries.py` asserts structurally that the
permission function takes who, which mode, which stance and which action, and
that there is nowhere for a budget to enter.

## 12. Performance

Full detail in the performance report. The four findings:

| | Before | After |
| --- | ---: | ---: |
| `validate_bars`, 200k bars, repeat | 1.383 s | **0.246 s** (5.6x) |
| Campaign test (exported transform memo) | 44.5 s | **8.2 s** (5.4x) |
| One grammar draw | ~100 ms | **0.13 ms** |
| 320-cycle campaign: unique constructions | 29 | **51** at the same wall clock |

Two of those were regressions this phase introduced and then caught by
measuring rather than by reasoning.

## 13. Latency

Backtest throughput on 40,000 bars is 32,000–95,000 bars/s depending on the
definition. A campaign cycle is 0.159 s in the expanded arm against 0.065 s in
the baseline — the difference is real work, not overhead: an assembled
construction carries more features, and the expanded arm reaches an experiment
on far more of its cycles.

## 14. Research effectiveness

320 cycles per arm through the real engine:

| | grammar off | expanded | throughput |
| --- | ---: | ---: | ---: |
| Unique structural constructions | **10** | **51** | **53** |
| Distinct mechanisms | 10 | 20 | 20 |
| Experiments | 80 | 121 | 116 |
| Cycles with nothing to propose | **240** | 0 | 0 |

The baseline arm stops at exactly ten — the number of written archetypes — and
then has nothing to do for 240 consecutive cycles.

## 15. Novelty measurements

Novelty detection was **not weakened**. `DUPLICATE_STATEMENT`,
`DUPLICATE_MECHANISM`, `FAMILY_DISTANCE`, `STRUCTURAL_FEATURE_CHANGE` and
`REFUSED_BY_DEFAULT` are unchanged.

`tests/research/test_adversarial_vocabulary.py` attacks the new vocabulary from
the other side: rename a construction, move a threshold, restate a hypothesis
with padding, repeat one paper twenty times, stuff an abstract with every
mechanism word. Each attack fails. 600 consecutive draws produced 600 distinct
signatures, and the signature deliberately excludes every number so a parameter
sweep cannot be counted as discovery.

## 15a. Temporal reservoir — verified, not rebuilt

`packages/forge/research/timescope.py` already holds the rule this phase would
otherwise have had to add: **a window is part of the claim**. It supports recent,
fixed range, full history, rolling, anchored, cross-regime, regime-selected and
event-selected windows; the scope is chosen before execution with its own
rationale, content-hashed into the preregistration, and re-derived by G1 at judge
time so a window that moved after the result was seen fails `CLAIM_MOVED`.

It is consumed rather than shelved: `MarketService` turns a scope into bars,
`plan.py` gates a plan carrying one, the action registry exposes it, and four
test files cover it. Nothing in this phase changed it, and nothing needed to.

## 16. Frontier movement

The expanded arm reached 51 frontier items (36 `FAILED`, 15 `EXHAUSTED`) against
the baseline's 10. A campaign now also raises questions from retrieved claims,
and reports an exhausted construction space as a finding rather than as another
refusal.

The Research Control Center gained a **construction vocabulary** panel, because a
duplicate rate means nothing without knowing how large the space being searched
is: 94% of proposals refused is either a saturated vocabulary or a disciplined
gate, and only that panel separates the two.

## 17. UX and quality of life

* The settings screen has no dead controls, and every model row says what would
  actually answer.
* Budget enforcement is legible: what it does, and what it does not.
* The vocabulary is visible rather than inferable from the source.
* External research states on its face that a claim is an input and not
  evidence — in the API payload, in the panel, and in the question text itself.

## 18. Security findings

No boundary moved, and `tests/research/test_expansion_boundaries.py` asserts each
one structurally rather than by reading the code:

* No new module imports the judge, risk, execution, prop desk, portfolio, ledger
  or the mode/permission layer.
* No new module imports a network, filesystem or subprocess module, or calls
  `eval`, `exec`, `compile`, `open` or `__import__`.
* The lead pipeline cannot create a family, register a template, or reach the
  promotion queue.
* Every assembled construction passes the same static guard as anything else
  this application executes, and the generated module imports nothing outside
  the allowed set.
* The routing layer holds no credential and cannot reach permissions or the
  action registry.
* Turning budget enforcement off changes no permission.

One genuine isolation defect was found and fixed: the test suite was writing the
developer's own `config/settings.json`, because `create_app` resolves it from the
repository rather than from the isolated workspace.

## 19. Test and CI results

* **Backend: 2,950 tests passing** (2,719 at the start of the phase; 231 added).
* **Frontend: 141 tests passing** (121 at the start; 20 added).
* `ruff check` clean.
* `mypy --strict` clean across 195 source files.
* TypeScript clean; production build passes.
* New suites: `tests/strategy/test_primitives.py`,
  `tests/research/test_grammar.py`, `tests/research/test_leads.py`,
  `tests/research/test_adversarial_vocabulary.py`,
  `tests/research/test_expansion_boundaries.py`,
  `tests/data/test_validation_identity.py`,
  `tests/research/test_effectiveness.py`,
  `tests/research/test_retrieval_policy.py`, `tests/api/test_model_routing.py`,
  `tests/api/test_settings_surface.py`,
  `apps/web/src/views/model-routing.test.tsx`,
  `apps/web/src/views/agent-effectiveness.test.tsx`.

## 20. Remaining limitations

Stated rather than worked around.

1. **The novelty gate reads prose.** It compares hypothesis text, and generated
   hypotheses share template wording. Its `STRUCTURAL` path compares feature sets
   exactly and is never reached for a fresh proposal, because a `Hypothesis`
   record does not store the features of the construction that implemented it.
   Giving it that column is the next bottleneck, and it is a schema change to a
   durable store.
2. **Allocation, not vocabulary, now decides how often a new construction is
   proposed.** A 320-cycle campaign reaches 51 of 408,471 reachable signatures.
   Most cycles go to refinement and robustness by design.
3. **A warm-up budget of 800 bars** excludes part of the reachable space —
   deliberately, because those cycles came back `BLOCKED` having measured nothing.
4. **Backtests are single-threaded.** Concurrency was examined and not attempted:
   generated strategy modules hold module-level state, so two concurrent
   backtests of one template would share it. Fixing it means changing the module
   protocol every hand-written template also implements.
5. **A second opinion runs only as a role, not as a per-call critic.**
   `agent_reviewer` critiques lineage and evidence quality on its own cycle;
   nothing reviews another model's answer inside the same call. The per-role
   critic control built for that was removed rather than left inert (§10).
6. **Nothing here was measured on real market data.** Every campaign number is
   from the seeded, deliberately edge-free synthetic series, which cannot clear
   G0. No candidate was promoted in any arm and none could be.
