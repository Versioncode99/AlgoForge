# AlgoForge — Full System Audit

**Date:** 2026-09-11
**Scope:** whole repository, traced from source rather than from README claims.
**Method:** every finding below names the file and line range it came from. Where a
claim in the README or an older doc was contradicted by the code, the code wins and
the contradiction is recorded.

---

## 1. What is actually here

### 1.1 Shape

```
packages/forge/          the domain libraries (143 Python modules, ~42k LOC)
apps/api/forge_api/      the FastAPI process and the autonomous engine (~13k LOC)
apps/web/                the React workstation (Vite + React 19)
tests/                   127 test modules
rules/, config/          version-controlled prop rule sets and provider config
strategies/              generated strategy artifacts (excluded from lint)
```

### 1.2 Domain packages, by weight

| Package | LOC | What it owns | State |
| --- | --- | --- | --- |
| `propdesk` | 12,265 | multi-account execution fabric, copy engine, allocation, risk, autonomy, audit | substantially complete |
| `research` | 8,506 | campaigns, frontier, hypotheses, novelty, literature, promotion, follow-ups | complete for **one** campaign |
| `strategy` | 5,409 | template catalogue, IR, exporter, guard, conformance, determinism | complete |
| `explain` | 1,718 | strategy passport, metric catalogue, progressive disclosure | complete |
| `prop` | 1,715 | single-account prop rules and path simulation | complete |
| `analytics` | 1,537 | regime attribution, resampling | complete |
| `execution` | 1,517 | OMS, order state machine, pre-trade gate | complete |
| `judge` | 1,506 | **G0–G13** and the statistics behind them | complete, authoritative |
| `modes` | 1,451 | the four built-in modes, their sections, permissions, expertise levels | complete but **hard-coded** |
| `workstation` | 1,286 | workspace/panel model, store, versions, templates | complete for panels, **no sidebar** |
| `memory` | 548 | durable failure record and the neighbourhood prune | complete |
| `agents` | 391 | LLM specialist role contracts | **not research agents** |

### 1.3 Runtime architecture as it actually runs

```
uvicorn → forge_api.main → forge_api.control (module-level singletons)
   │
   ├── AutonomousEngine            apps/api/forge_api/engine.py
   │     ├── N daemon threads, one per "worker"          (engine.py:_loop)
   │     ├── EngineState — a mutable dataclass in memory (engine.py:87)
   │     ├── Experiments (SQLite)  — exact-duplicate claims
   │     ├── ResearchMemory (SQLite) — neighbourhood prune
   │     └── director: ResearchDirector | None
   │
   ├── ResearchDirector            apps/api/forge_api/director.py
   │     └── self._campaign_id: str | None      ← ONE campaign
   │
   ├── CampaignService             apps/api/forge_api/campaigns.py
   │     └── CampaignStore.active() → the single row WHERE status='running'
   │
   ├── AgentService                apps/api/forge_api/agent_service.py
   │     └── LLM specialists on a daily call cap; not research workers
   │
   ├── PropDeskService / FundService / Orchestrator / Assistant
   └── WorkspaceStore              packages/forge/workstation/store.py
```

Every one of those is a **module-level singleton constructed at import time** in
`forge_api.control`. There is exactly one engine, one director, one active
campaign and one workspace store per process.

---

## 2. The reported bug: `Engine: RUNNING / Skipped by memory: 1,290`

This was traced end to end. It is three separate defects that compound.

### 2.1 What `Skipped by memory` actually counts

`EngineState.skipped_by_memory` (`engine.py:102`) is incremented in **two
unrelated places**:

1. **`engine.py:534`** — the research director returned a `Refusal`.
   A `Refusal` is produced by **eleven different code paths** in `director.py`
   (lines 301, 328, 391, 416, 421, 439, 525, 568, 599, 628, 822, 938), which
   include:
   - the campaign hit a stopping criterion and was **auto-completed and detached**
     (`director.py:296-301`) — this is *campaign exhaustion*, not a memory skip;
   - a proposal builder **raised an exception** (`director.py:328`) — that is an
     *error*, not a memory skip;
   - every archetype is already represented (`director.py:391`);
   - the novelty gate refused a restatement (`director.py:416`, `822`);
   - a template failed the static guard or the pilot (`director.py:439`);
   - the frontier had no eligible item (`director.py:525`, `568`, `599`, `628`);
   - literature retrieval failed (`director.py:938`).

2. **`engine.py:665`** — `Experiments.reserve` returned `None`, meaning this exact
   `(scope, template, parameters)` triple was already claimed
   (`experiments.py:157-244`). This *is* a duplicate skip.

A third, genuinely distinct counter, `skipped_by_region` (`engine.py:105`), counts
`ResearchMemory.prune` hits (`memory/research.py:284`) and is displayed only as part
of the derived `compute_saved`.

**So `Skipped by memory: 1,290` is an undifferentiated sum of campaign exhaustion,
director errors, novelty refusals, empty-frontier cycles and true duplicates.** It
cannot be used to diagnose anything, which is precisely the complaint.

### 2.2 Why the engine keeps saying RUNNING

`EngineState.running` (`engine.py:88`) is set `True` in `start()` (`engine.py:212`)
and only set `False` in `stop()` or when the last worker leaves `_loop`
(`engine.py:381-384`). It means **"threads exist"**, never **"work is happening"**.

The UI reads exactly that bit:
`apps/web/src/views/Pipeline.tsx:167-168` renders `RUNNING` from `state.running`.

Meanwhile `_cycle` has **six early-return paths** that consume a full cycle and
produce nothing:
- director refusal (`engine.py:532-540`)
- region prune (`engine.py:625-634`)
- duplicate reservation (`engine.py:663-671`)
- plus three downstream `return`s on insufficient trades / no-data.

After the refusal the worker falls through to
`self._stop.wait(self.state.config.cycle_seconds)` (`engine.py:377`) and does it
again. With 8 workers at a 4-second cycle that is **7,200 refusals an hour** and
the interface says RUNNING throughout. 1,290 is roughly 11 minutes of it.

### 2.3 The deadlock that produces a permanent refusal loop

Two mechanisms make refusals *permanent* rather than transient:

**(a) Campaign auto-completion without engine stop.**
`director.next_candidate` (`director.py:295-301`) detects exhaustion, sets the
campaign to `completed`, calls `self.detach(why)` — and returns a `Refusal`. On
the *next* cycle `self.campaign()` is `None`, so `next_candidate` returns `None`,
and the engine silently falls back to **the original uniform random template
draw** (`engine.py:553-596`). The campaign is over; the engine keeps running; the
interface still shows the engine as RUNNING and offers no signal that research
direction has stopped. Every subsequent candidate is drawn at random from the same
finite template catalogue against a `scope` that has already claimed most of it, so
almost all of them hit `reserve` → `None` → `skipped_by_memory`.

**(b) `TEMPLATE_WIDE` prune reach.**
`memory/research.py:85-86` gives `LOOKAHEAD` and `SAFETY` a reach of
`TEMPLATE_WIDE`, and `prune` returns immediately for those
(`memory/research.py:302-311`). One such failure permanently bars **every**
parameter set on that template, in that scope, forever. There is no expiry, no
override, and no way for the interface to show that a template has been condemned.

**Neither (a) nor (b) changes the displayed state.** That is the bug.

### 2.4 Is the skip accounting itself wrong?

Yes, in three further ways:

- A director refusal **is charged as a memory skip even when nothing was matched**,
  so "duplicates prevented" is inflated by empty-frontier and error cycles.
- `compute_saved = skipped_by_memory + skipped_by_region` (`engine.py:134`)
  therefore claims compute was saved on cycles where nothing would have been run.
- `CampaignProgress.consecutive_duplicates` is bumped only for refusals flagged
  `duplicate=True` (`director.py:331-341`), so the exhaustion criterion
  `max_consecutive_duplicates` (`campaign.py:192`) never fires for the *most
  common* refusal — an empty frontier. A campaign can spin on empty-frontier
  refusals indefinitely without ever declaring itself exhausted.

### 2.5 Can legitimate novelty be suppressed?

- `assess()` (`novelty.py:308`) compares the proposal against families, templates
  and hypotheses using shingle overlap. A genuinely new mechanism phrased in
  familiar words scores high on `statement` and can be refused. There is **no
  record of the refusal**, no similarity band, and no notion of "retry permitted".
- A failed hypothesis writes a `FailureRecord`; a legitimate follow-up at a nearby
  parameter set is pruned by `prune()` with no way to say "this follow-up is
  deliberately inside that region because the mechanism changed".
- A skip **consumes a research cycle** and **creates nothing** — no frontier item,
  no hypothesis, no lineage row. The frontier therefore does not learn that the
  region was refused, which is why the same refusal repeats.

---

## 3. Architectural bottlenecks — where "one" is assumed

| Assumption | Where | Consequence |
| --- | --- | --- |
| one engine | `control.py` module singleton | no way to run two datasets at once |
| one campaign | `CampaignStore.active()` `campaign.py:379`, `ResearchDirector._campaign_id` `director.py:233` | `set_status('running')` raises `CampaignError` if another campaign runs (`campaign.py:397-402`) |
| one agent pool | `AgentService._states` keyed by skill name `agent_service.py:57` | agents are LLM roles, not research workers; cannot be scaled, have no campaign, no lease, no heartbeat |
| one workspace layout per mode | `modes/models.py` `workspace_template` | switching mode switches layout |
| one research queue | worker index → fixed policy `engine.py:_cycle` line 1 | policy is a function of thread id, not of research need |
| one sidebar per mode | `modes/models.py:135-220` `Section(...)` literals; `App.tsx:160-168` renders `descriptor.sections` | a user cannot put Research next to Prop Accounts without switching modes |

`Experiments` scope (`engine.py:286-290`) is
`dataset:max_bars:data_version:catalog_version:contract-units-v2` — **it does not
include the campaign**. Two campaigns on the same dataset would share one duplicate
namespace and silently steal each other's claims. This is the single change that
most blocks multi-campaign work.

---

## 4. Persistence and concurrency

**Durable (SQLite, survives restart):** experiments, research memory, campaigns,
frontier, hypotheses, journal, sources, promotion queue, workspaces + versions,
prop desk stores, settings, activity.

**Not durable (in-process only):**
- `EngineState` — every counter resets to zero on restart, so the interface's
  "Skipped by memory" is per-process and cannot be compared to the campaign's own
  progress row.
- `ResearchDirector._allocation`, `_generated`, `_topics`, `_cycles_since_adapt`.
- `AgentService._states`.
- Worker→strategy assignment `_active`.

**Concurrency:** `Experiments.reserve` takes `BEGIN IMMEDIATE` and is safe
(`experiments.py:195`). `PromotionQueue.release_running` and
`Experiments.reclaim_abandoned` handle interrupted runs at start-up. There is **no
lease with an expiry** anywhere — a claim held by a worker that hangs is only
released when the *process* restarts, not when the *worker* dies. There is no
heartbeat, so a hung worker is indistinguishable from a busy one.

---

## 5. Validation accounting

`PromotionQueue` records `QUEUED / RUNNING / DECIDED / ABANDONED` and outcomes
`PASS / FAIL / INCONCLUSIVE` (`promotion.py:53-65`). The campaign overview exposes
`pending` and `outcomes` (`campaigns.py:104-107`).

Gaps:
- there is no `BLOCKED` state — a candidate refused by `Prerequisites` is simply
  never queued, so it is invisible; the interface therefore cannot distinguish
  "nothing has been validated" from "nothing was eligible";
- `CampaignProgress` has `validation_candidates` and `validated` but no
  attempts/failed/blocked triple;
- the Research Campaign screen renders the queue but the top-line figures do not
  separate attempts from passes.

**G0–G13 itself is sound.** `packages/forge/judge` is the only thing that produces
a verdict, the director explicitly cannot construct one (`director.py:93-101`
defines a three-field `_Gate` shim precisely so it cannot), and nothing in the
research layer writes to the judge. This must be preserved exactly.

---

## 6. Workspaces and the mode problem

`packages/forge/workstation` has a genuinely good panel model: 12-column grid,
immutable `Workspace` with copy-on-write panel operations, versioning with restore,
export/import, active/default tracking, session restore (`store.py:371`).

What it does not have:
- `description`, `icon`, `kind` (BUILT_IN / USER_CREATED / CLONED), `pinned`;
- **any sidebar model at all** — navigation comes from `forge.modes`, which is a
  hard-coded list of `Section(...)` literals per mode;
- links to campaigns or accounts.

The consequence is the reported UX complaint: the sidebar is a property of the
*mode*, so wanting Prop Accounts and Research Agents side by side means switching
modes, and switching modes swaps the whole navigation and layout. `App.tsx:225`
renders a "Switch" button that returns to the four-way chooser.

---

## 7. Prop Desk, execution and risk

Audited and found substantially real:
- provider abstraction with `DeclaredAdapter` (declares capability without
  pretending to connect) and `SimulatedAdapter` (`propdesk/adapters/`);
- order state machine and reconciliation (`propdesk/orders.py`, `reconcile.py`);
- copy engine with per-firm policy states including an explicit `UNKNOWN` that does
  not become `ALLOWED` (`propdesk/policy.py`);
- risk modes manual / adaptive / AI-managed with hard ceilings (`propdesk/risk.py`,
  `scaling.py`, `autonomy.py`);
- consent and audit (`propdesk/consent.py`, `audit.py`).

This area needs integration into the new workspace/sidebar model rather than
rebuilding.

---

## 8. UX problems found

1. `Engine: RUNNING` with no work happening (§2.2).
2. `Skipped by memory` is a meaningless aggregate (§2.1).
3. Campaign silently auto-completes; nothing on the engine screen says so (§2.3a).
4. A condemned template is invisible (§2.3b).
5. Validation shows a queue but not attempts/passes/failures/blocked (§5).
6. Mode switching is required for ordinary feature access (§6).
7. Research metrics are template-centric: the dashboard counts templates, which is
   a proxy for diversity that stops being true the moment the director composes
   new templates from the same archetypes.

---

## 9. Dangerous assumptions

- **"running" means "working"** — it does not, and the interface repeats the lie.
- **"a skip saved compute"** — a refusal on an empty frontier saved nothing.
- **"one campaign is enough because they share the holdout"** — the real constraint
  is the burn-once holdout ledger and the `Experiments` scope, both of which can be
  partitioned per campaign. The single-campaign rule is stronger than the
  constraint requires.
- **"the director can always be re-attached"** — `detach` clears the id with no
  record that a campaign *was* attached, so a restart loses the association.
- **`TEMPLATE_WIDE` is forever** — a lookahead bug fixed in a template still leaves
  the template condemned in every existing scope.

---

## 10. Proposed target architecture

```
                    ResearchOrchestrator          (new)
                    ├── runtime state machine     (new)
                    ├── campaign registry         (extends CampaignStore)
                    ├── agent registry + leases   (new)
                    └── no-progress watchdog      (new)
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   Campaign A            Campaign B            Campaign C
   ResearchDirector      ResearchDirector      ResearchDirector
        │                     │                     │
   Agents(role,lease)    Agents(role,lease)    Agents(role,lease)
        └─────────────────────┼─────────────────────┘
                              ▼
                   Shared research ledger
              (frontier · hypotheses · journal ·
               sources · skip ledger · knowledge)
                              │
                              ▼
                    PromotionQueue → Judge (G0–G13)
                              │
                              ▼
                      Strategy library → Prop Desk
```

Concrete changes, in dependency order:

1. **Runtime truth.** `EngineRuntimeState` enum, per-worker heartbeat with a lease
   expiry, a no-progress watchdog that diagnoses and either re-directs or reports
   `IDLE` / `BLOCKED` / `EXHAUSTED`. `running: bool` becomes derived, never set.
2. **Skip accounting.** A persisted `SkipLedger` with a `NoveltyLevel` band, the
   matched prior object, a similarity score and `retry_permitted`. Every skip site
   writes one. `skipped_by_memory` is replaced by a breakdown.
3. **Scope by campaign.** `Experiments` scope gains the campaign id so two
   campaigns cannot consume each other's claims.
4. **Multi-campaign.** Remove the single-running constraint; `ResearchDirector`
   becomes one-per-campaign, owned by the orchestrator; workers are assigned a
   campaign by the orchestrator rather than a policy by their thread index.
5. **Agents.** A real `ResearchAgent` record with role, state, lease, heartbeat and
   task, persisted, with claims so two agents cannot run the same experiment.
6. **Workspaces.** Extend the model with kind/description/icon/sidebar/pinned and a
   sidebar item registry that is a function of *capability*, not of mode.
7. **Surfaces.** A Research Control Center and an Agent Monitor reading the new
   accounting, and honest validation figures.
