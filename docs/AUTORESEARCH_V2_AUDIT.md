# AutoResearch V2 — Audit

**Phase 0. No behaviour was changed while writing this.**

Branch `feat/autoresearch-v2`, cut from `feat/openterminal-workstation-integration`
(`b6b5f8c`) rather than from `main`, because Phase 15 of the brief asks the
freshness envelope to be wired into the real data path and that envelope was
built on the previous branch. Working from `main` would mean building it twice.

What follows is an inspection of the code, an inspection of
`EvoMap/AutoResearch` (Apache-2.0, cloned and read), and a per-phase statement
of what already exists, what does not, and what must not be rewritten.

---

## 0. The headline finding: the brief's premise is inverted

The brief's central instruction is:

> **16 YEARS OF DATA IS A RESERVOIR — NOT THE DEFAULT BACKTEST WINDOW**
> … DO NOT automatically backtest every strategy against all 16 years.

**AlgoForge does not do this, and never has.** The opposite is true, and it is
worse.

`AutonomousEngine._loop` (`apps/api/forge_api/engine.py:479`) loads bars exactly
once per engine run:

```python
self._loaded = self.market.load(
    self.state.config.dataset, limit=self.state.config.max_bars
)
```

and `MarketService.load` (`apps/api/forge_api/market.py:207`) resolves `limit`
as:

```python
window = frame.tail(limit) if limit and limit < len(frame) else frame
```

`EngineConfig.max_bars` defaults to **250,000**. At roughly 347,760 one-minute
bars per year for a CME equity-index future, that is **about nine months** —
and it is `tail()`, so it is always the most *recent* nine months.

So the actual behaviour is:

- every experiment in a campaign runs on **one identical window**;
- that window is **the most recent ~0.7 years**, whatever the hypothesis;
- the 16-year archive is **barely touched**;
- and `self._loaded` is cached for the life of the run, so a campaign cannot
  vary it even across workers.

A day-of-week seasonality hypothesis, which the brief itself gives as an example
needing 10–16 years, currently gets nine months and **nobody is told**. That is
not brute force over a reservoir; it is a silent, invisible, one-size window.

The system the brief asks for — an explicit, per-hypothesis, frozen-before-results
time scope — is exactly the right fix for the real problem too. The work barely
changes. The justification does, and this document and the final report state
the real behaviour rather than the assumed one.

### The second dead field

`Campaign.start_date` and `Campaign.end_date` (`packages/forge/research/campaign.py:162`)
are accepted by the API, validated, stored in their own columns, round-tripped
through `as_dict()`, copied by `duplicate()` — and **read by nothing**:

```
$ grep -rn '\.start_date|\.end_date' --include=*.py apps/ packages/ tests/ \
    | grep -v campaign.py | grep -v 'start_date='
(no output)
```

An operator who sets a campaign's date range today gets the most recent 250,000
bars regardless, silently. This is the same species of defect as the `link_group`
badge found in the previous phase: a field written, exposed, and never read,
with an interface implying it means something.

It is also where `ExperimentTimeScope` should land — the plumbing exists up to
the point of consumption.

---

## 1. What already exists, and works

Substantial parts of this brief are already built. Listing them precisely
matters, because §"NON-NEGOTIABLE" rule 18 forbids a parallel research
architecture and the fastest way to build one by accident is not to look.

| Brief phase | Already in AlgoForge | Where | State |
|---|---|---|---|
| 5 — Research signal fabric | External retrieval with un-fakeable provenance | `research/literature.py` (615 lines), `research/sources.py` | **Works.** "Every stored source is something a fetch returned. There is no code path that constructs a citation." No network ⇒ empty list and a blocked frontier item, never a plausible-looking paper. |
| 10 — Failure-driven research | Failure → question → derived hypothesis | `research/followup.py` (378 lines) | **Works.** |
| 11 — Novelty | 5-category novelty gate over claim/structure/mechanism | `research/novelty.py` (580 lines) | **Works.** Extended last phase by `research/skips.py` with 8 `NoveltyLevel` bands and a refusal ledger. |
| 12 — Experiment allocation | Information value vs compute cost ranking | `research/information.py` | **Works.** |
| 2 — Research states | `HypothesisStatus` (8), `FrontierState` (9) | `research/hypotheses.py`, `research/frontier.py` | **Works.** Two state machines, deliberately separate. |
| 9 — Critic | Four specialists, disagreement preserved, cannot move a number | `agents/debate.py` | **Works, but post-verdict only.** |
| 16 — Provenance | Content-addressed hashing, receipts, `provenance/` | `contracts/hashing.py`, `research/split.py` | **Works.** |
| 23 — Model routing | 9 roles, per-role model, credentials by env-var name only | `forge_api/settings_store.py:39` | **Works.** |
| 18 — Frontier | `ResearchFrontier` with counts, kinds, per-campaign scoping | `research/frontier.py` | **Works.** |
| 19 — Control center | Panels, action registry, campaign screens | `apps/web`, `forge_api/actions.py` | **Works.** 150 actions incl. campaign lifecycle. |
| 20 — Campaign design | Objective, budget, allocation, stopping, capabilities, agents | `research/campaign.py` | **Works**, minus time scopes. |
| 24 — Security | AI cannot call `protected`; external content is data | `modes/permissions.py` | **Works.** |
| G0–G13 | The gate ladder | `judge/engine.py` | **Authoritative. Do not touch.** |

**None of these should be rewritten.** Every one is a place to extend.

### The most important existing mechanism for this task

`Preregistration` (`packages/forge/contracts/models.py:15`) freezes
`hypothesis + mechanism + falsification`, content-hashes them, and is stored
append-only (`forge_api/preregistration_store.py` — *"Records are append-only
and never replaced. Overwriting one would let a second freeze launder a moved
claim into a held one"*). **G1 re-derives the hash at judge time and fails
`CLAIM_MOVED` if anything shifted** (`judge/engine.py:130`).

That is already the exact enforcement Phase 14 asks for. Putting the time scope
*inside* the preregistered payload means "changed the window after seeing the
result" becomes a G1 failure automatically — **no second freeze mechanism, no
new gate, no weakening of anything.** This is the single highest-leverage
integration point in the whole brief.

---

## 2. What does not exist

| Brief phase | Missing | Notes |
|---|---|---|
| **Time scope (the core)** | **Everything.** No `ExperimentTimeScope`, no selection method, no rationale, no train/validation/holdout windows chosen per experiment, no record of what was selected or why. | `chronological_split` partitions whatever window it is handed 60/20/20 with purge gaps, but the *window itself* is never chosen. |
| 6 — Research plan | No plan object. `Preregistration` covers the claim only — not dataset, window, costs, success/failure criteria, budget. | |
| 7 — Plan gate | No deterministic accept/reject on a plan. | |
| 8 — Pilot | Absent. Every experiment is full-size. | |
| 9 — Plan critic | `debate.py` critiques a *verdict*. Nothing critiques a *plan* — mechanism, falsifiability, window choice, leakage, sample sufficiency — before compute is spent. | |
| 15 — Freshness enforcement | `forge.data.freshness` exists (previous branch) and **nothing consumes it.** `MarketService` returns bare frames. | Known and documented as unconsumed. |
| 3 — Research question first | The director proposes candidates from archetypes/templates; there is no first-class "research question" preceding a strategy. | |

---

## 3. The current autonomous loop, as it actually runs

Read from `engine.py` and `director.py`:

```
engine.start(config)
  └─ load dataset ONCE: tail(max_bars)      ← the whole temporal design
     └─ chronological_split 60/20/20 + purge gaps
        └─ per worker, per cycle:
           ├─ orchestrator picks a campaign      (previous phase)
           ├─ director.next_candidate(rng, worker, campaign, role)
           │    ├─ frontier item / archetype / template
           │    ├─ novelty check → Refusal(kind, level, …)
           │    └─ downgrade-pursuit if a family proposal collides
           ├─ freeze_claim(spec, params)         ← G1's preregistration
           ├─ backtest over the development partition
           ├─ judge → Verdict (G0–G13)
           ├─ record outcome, skips, progress
           └─ followup.derive() on failure
```

### Bottlenecks

1. **One window per run, chosen by a bar count.** Described above.
2. **No pilot.** Every candidate costs a full backtest over the full loaded
   window, so an idea that could be falsified on three months costs the same as
   one that needs ten years.
3. **Candidate-shaped, not question-shaped.** The unit of work is a strategy
   spec. A hypothesis that cannot yet be expressed as a template has nowhere to
   live.
4. **Critique arrives after the compute is spent**, not before.
5. **Freshness is not consulted at all** on the read path.

---

## 4. AutoResearch: what is worth taking

Read from `ARCHITECTURE.md` and the repository layout. Apache-2.0; **no code is
proposed for copying** — the two systems' units of work differ (an ML-paper
idea versus a quantitative hypothesis with a gate ladder behind it).

| AutoResearch concept | Verdict | AlgoForge landing |
|---|---|---|
| `plan → plan gate → … → pilot → analysis → critic → revise or stop → close` | **ADOPT the ordering** | The plan gate and pilot are the two missing stages. |
| Negative results are legitimate terminal states, given complete run evidence | **ADOPT** — already half-true via `followup.py` | Make it explicit in plan outcomes. |
| Pilot before scaling | **ADOPT** | Must be hypothesis-aware, not "2 years first for everything" — the brief says so and it is right. |
| Engine owns completion conditions; the coordinator cannot bypass them; `verify-close` only accepts terminal states the records support | **ADOPT the principle** | This is already how the judge relates to the engine. Extend it: an agent may not declare a plan passed. |
| Each collector may fail independently; failures are recorded and not disguised as success | **ADOPT** — already the rule in `literature.py` | No change needed; worth asserting in a test. |
| Role → ordered model aliases → ordered routes; config stores env-var *names*, never values | **ADAPT** | AlgoForge stores per-role models already and resolves credentials server-side. `providers.py` deliberately has **no** fallback chain, for a documented reason (silently rerouting means silently substituting the model). Keep that. |
| Multi-model independent ideation and cross-review; insufficient votes ⇒ recorded not-passed, never auto-promoted | **ADAPT** | Only honest where genuinely independent models exist. Two aliases of one model are not two opinions. |
| Freshness refresher (are the baselines stale?) | **ADAPT** | AlgoForge's freshness question is about *data admissibility*, which is a harder and more consequential version. |
| SHA256 provenance binding, re-verified at init | **ADOPT** | `content_hash` already does this; extend the bound payload. |
| `state.md` / `decisions.log` / `workflow_queue.json` as recoverable state | **REJECT the mechanism, keep the goal** | AlgoForge persists to SQLite with leases and heartbeats already. Files would be a second, weaker state store. |
| Reddit / Hacker News / GitHub Trending as research signals | **REJECT** | Appropriate for ML-paper discovery, not for evidence about market microstructure. `literature.py`'s Crossref path is the right shape here. |
| Shell-executing runtime that writes and runs code | **REJECT** | AlgoForge's agent boundary is the action registry. Widening it to shell would discard the entire permission model. |

---

## 5. Exact files and classes to modify

**New:**

- `packages/forge/research/timescope.py` — `TimeScope`, `SelectionMethod`,
  `WindowSpec`, selection + rationale + provenance.
- `packages/forge/research/plan.py` — `ResearchPlan`, plan gate, outcomes.
- `packages/forge/research/pilot.py` — pilot design and promotion decision.

**Extend, do not replace:**

- `packages/forge/contracts/models.py` — `Preregistration` gains the frozen
  time scope so G1 enforces it. *The gate itself is not modified.*
- `apps/api/forge_api/market.py` — `load_scope()` beside `load()`;
  `load_range()` already exists and does most of the work.
- `apps/api/forge_api/engine.py` — per-experiment window instead of one cached
  `self._loaded`.
- `packages/forge/research/campaign.py` — allowed scopes; make `start_date`/
  `end_date` mean something or remove them.
- `packages/forge/research/frontier.py`, `hypotheses.py` — plan/pilot states.
- `apps/api/forge_api/actions.py` — verbs for plans, scopes, pilots.

**Do not touch:** `packages/forge/judge/` (the ladder), `modes/permissions.py`,
`research/novelty.py`, `research/literature.py`, `research/followup.py`,
`research/information.py`, `agents/debate.py`.

---

## 6. Risks in this work

- **Per-experiment windows multiply data loading.** One cached frame becomes
  many slices. `load_range` exists and slices a pandas frame, but the engine
  currently holds one `self._loaded` under a lock; making that per-scope needs
  a cache keyed by scope or it will re-read the archive per cycle.
- **`chronological_split` assumes it owns the whole window.** A scope that
  supplies its own train/validation/holdout must not be split a second time.
- **Multiple-testing accounting must grow.** If the engine may choose windows,
  window search *is* selection exposure, and the existing trial counting has to
  see it or the deflated Sharpe understates.
- **No real archive is present in this environment** (`data/market/databento/`
  is empty), so integration work must run on the synthetic dataset, exactly as
  the previous phases did. Claims about 16-year behaviour will be about the
  *mechanism*, tested with synthetic bars and explicit date arithmetic — not
  about measured results on real NQ history.
