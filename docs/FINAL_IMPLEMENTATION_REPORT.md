# Final Implementation Report

**Branch:** `claude/nifty-ritchie-1rf1tk`
**Baseline:** `2156413`
**Scope:** 45 files, of which 25 are new.

---

## 1. Executive summary

The complaint that started this was one screen: `Engine: RUNNING`,
`Workspace: REPO`, `Skipped by memory: 1,290`. Tracing it found that all three
figures were, in different ways, not answers.

`RUNNING` was a boolean meaning "worker threads exist". `Skipped by memory` was
one counter summing five unrelated situations. And the campaign that had been
directing the search had auto-completed, detached itself, and left the engine
silently reverting to the uniform random template draw it used before campaigns
existed — spending compute against a claim namespace that had already consumed
most of the catalogue, while the interface said RUNNING throughout.

Underneath that were five structural assumptions of *one*: one engine, one
campaign, one director, one agent pool, one sidebar per mode.

This change makes the runtime state truthful, replaces the skip counter with a
durable and explainable ledger, removes the single-campaign and single-agent
constraints, and moves navigation from the mode to the workspace. It adds no
capability to the research layer that could reach a verdict, a gate, a risk limit
or an order, and the G0–G13 ladder is byte-identical to the baseline.

**Verification:** full Python suite green (134 new tests among them), 86 web
tests green, ruff clean, mypy strict clean across 184 source files, and visual QA
at four viewport widths against a live API with a running engine.

## 2. Original architecture

```
uvicorn → forge_api.control (module-level singletons)
   ├── AutonomousEngine       N threads; state = a mutable dataclass; running: bool
   ├── ResearchDirector       self._campaign_id: str | None          ← ONE
   ├── CampaignStore.active() SELECT ... WHERE status='running' LIMIT 1
   ├── AgentService           LLM specialists; not research workers
   └── WorkspaceStore         panels only; navigation came from forge.modes
```

A worker's research policy was `POLICIES[worker_index % len(POLICIES)]` — worker
3 did neighbourhood search because it was worker 3.

## 3. New architecture

```
                    ResearchOrchestrator            (new: scheduler only)
                    ├── RuntimeMonitor              (new: heartbeats → state)
                    ├── AgentRegistry               (new: roles, leases, claims)
                    └── CampaignStore.running()     (many)
                              │
        ┌─────────────────────┼─────────────────────┐
   Campaign A            Campaign B            Campaign C
   director state        director state        director state
   Agents(role,lease)    Agents(role,lease)    Agents(role,lease)
        └─────────────────────┼─────────────────────┘
                              ▼
       frontier · hypotheses · journal · sources · SkipLedger
                              ▼
                   PromotionQueue → Judge (G0–G13)   ← unchanged
                              ▼
                  StrategyLibrary → Prop Desk        ← unchanged
```

## 4. Autonomous research changes

`packages/forge/research/runtime.py` (new, ~600 lines).

Thirteen named `RuntimeState`s replace the boolean. Workers report a heartbeat
per stage and an `Outcome` per cycle; the state is **derived** and `RUNNING` is
returned only when a worker reported `PROGRESS` inside the no-progress window.
No other path can produce it.

`_cycle` was changed to return `(Outcome, reason)` from all seven of its terminal
paths — six of which used to `return None`, indistinguishable to the loop from a
completed experiment.

The watchdog names the specific stall (`duplicate_loop`, `no_work`, `exhausted`,
`blocked`, `cycle_errors`, `workers_dead`, `idle_no_outcomes`) with a remedy, and
logs the **transition** rather than repeating itself.

`RuntimeMonitor.backoff` paces the loop: the wait grows towards 30 s as barren
cycles accumulate and snaps back to full rate the moment anything progresses.
Found by load testing, where 768 cycles had produced 749 barren ones.

**The silent mode change is closed.** A campaign that hits a stopping criterion
now detaches with `exhausted=True`, and `next_candidate` keeps returning
`CAMPAIGN_EXHAUSTED` until something else is attached, instead of returning
`None` and letting the engine revert to an undirected random search.

## 5. Memory and deduplication

`packages/forge/research/skips.py` (new, ~480 lines).

`skipped_by_memory` became seven counters plus a durable `SkipLedger`. Each
refusal records the subject, the reason, the matched prior object, the
similarity, a `SkipKind`, a `NoveltyLevel`, whether a retry is permitted and
under what condition, and an occurrence count — identical refusals collapse onto
one row, because eight workers refusing the same proposal is one fact.

The eight-band hierarchy runs `EXACT_DUPLICATE → … → NOVEL_FEATURE`.
**`SAME_CONSTRUCTION` is deliberately not refused by default:** refining
parameters inside a known construction is ordinary research, and what bounds it
is the budget, not the duplicate gate.

`compute_saved` no longer counts cycles where nothing would have run. `useful`
and `wasted` are separated, because a refusal that declined an experiment and one
that happened because the frontier was empty are opposite facts.

**Deduplication that no longer prevents discovery:** the novelty gate refuses a
*family* proposal that restates a known explanation, and its verdict carries a
downgrade in its own words — "this is a new hypothesis within a known
explanation, which is worth testing". The director used to discard that and spend
the cycle on nothing. It now pursues the downgrade at the level the gate assigned;
only a verdict of `PARAMETER` refuses outright.

## 6. Multi-campaign

The rule forbidding a second running campaign cited a constraint that did not
exist: the holdout ledger is keyed by strategy lineage, and two campaigns produce
different lineages. What they actually shared was the duplicate-claim namespace.

- `Experiments` gains a `campaign_id` column, included in the claim identity when
  set (omitted when empty, so standalone keys are unchanged).
- **The trial count is deliberately not partitioned.** `count(scope)` stays
  dataset-wide and the judge calls it that way: every trial against this series
  is a trial whoever ran it, and narrowing it per campaign would lower the
  Deflated Sharpe's hurdle exactly as more campaigns searched the same data.
- `CampaignStore` gains `running()`, `duplicate()`, `archive()`, `restore()`,
  `prioritise()`, `rename()`, `set_agent_target()`. A duplicate copies
  configuration only — never progress, never findings.
- `ResearchDirector` serves any number of campaigns; per-campaign state lives in
  `_CampaignState` and the campaign a thread is serving is a thread-local.

**Resumability bug fixed:** `_catalog_version` hashed all of `TEMPLATES`, which
the director adds to as it composes, so every restart after a campaign generated
a template started a fresh experiment scope and re-ran everything. Found by the
restart test.

## 7. Multi-agent

`packages/forge/research/agents.py` (new, ~620 lines). Ten roles, nine states,
persisted with heartbeat, progress mark, claim, compute budget and error count.

Claims are unique per `(campaign, subject, replica_of)` and carry an **expiry**,
so an agent that dies releases its work when the lease lapses rather than when
the process restarts. A deliberate replica is a separate claim with `replica_of`
set, so "we ran it twice" can never read as two independent findings.

Roles **bias** the allocation draw and can never veto a bucket: four
falsification agents must not silently mean a campaign that never proposes.

`capacity_for` answers honestly — two agents per core, capped at 64 — and returns
a sentence explaining any shortfall rather than rendering rows that do nothing.

## 8. Research orchestration

`packages/forge/research/orchestration.py` (new, ~330 lines). A scheduler and
nothing else: it decides which campaign a worker serves, never what to research
and never what a result means.

`weight = max(1, priority) * health`, where health is the share of the last 60
cycles that produced something, floored at 0.15. Every running campaign keeps at
least one worker, because a campaign starved to zero can never demonstrate that
it recovered. An unproven campaign scores 1.0 for its first window.

Verified live: two campaigns at priority 70/30 received 3 and 1 of four workers;
after 50 barren cycles on the first and 50 productive on the second, the split
inverted to 3/5.

## 9. Workspace system

`packages/forge/workstation/sidebar.py` (new, ~420 lines). `CATALOGUE` is the
**union** of every destination any mode offers — 49 — derived from the manifests
rather than written again. `Workspace` gains `description`, `icon`, `kind`,
`sidebar`, `pinned`, `campaign_ids`, `account_ids`, `mode`, migrated by
`ALTER TABLE`.

`rail()` falls back to the mode's sidebar, so every existing arrangement opens
unchanged and becomes editable the moment somebody changes it.

**Validation bug fixed:** every mutation used `model_copy`, which in pydantic v2
does not re-run field validators — the duplicate-route check and both ceilings
only ran at construction. Found by the bounds test.

## 10. Custom sidebar

Add · remove · move · reorder · rename · group · collapse · pin · hide, on items
and groups, plus reset-to-default. Eighteen actions in the **shared registry**
with HTTP routes over them; there is no AI-only path.

Composed live through the action registry:

```
My Prop Research (NQ)
  Desk: Prop Desk · Copy Trader · News        Risk: Risk
  Autonomous: Research Control* · Research Campaign
  AI: Agents      Strategy: Validation        MY TRADING: NQ Chart
```

Four modes' destinations, one arrangement, no switching.

## 11–14. Prop Desk, execution fabric, copy trader, allocation

Audited and found substantially complete and honest: declared adapters refuse
every command, only the simulator executes and labels fills as simulated, firm
policy has an `UNKNOWN` that never becomes `ALLOWED`, and the order state machine
handles partial fills, duplicate events and reconciliation.

This change **integrated** rather than rebuilt: every Prop Desk destination is in
the sidebar catalogue, and `link_account_to_workspace` associates an account with
a screen without granting anything. See `docs/PROP_DESK_ARCHITECTURE.md`.

## 15–16. AI risk and autonomous deployment

Audited; unchanged. Manual, adaptive and AI-managed all move one number — the
share of an account's buffer an allocation may cost — and every ceiling is
checked after the mode has produced it. Autonomous deployment cannot reach a
venue in this build and reports that refusal as a mandatory control that did not
pass. See `docs/RISK_AUTOMATION_ARCHITECTURE.md`.

The two new sources of authority this change introduced — campaign priority and
agent roles — reach nothing in risk, and `tests/research/test_boundaries.py`
asserts it structurally.

## 17. UX

See `docs/UX_IMPLEMENTATION_REPORT.md`. Three interface lies removed; complexity
made legible rather than hidden; three real CSS/behaviour bugs found by looking
at the rendered result.

## 18. Security

No credential, token or secret is read, logged or serialised by anything added
here. The skip ledger stores template keys, parameter values and prose reasons;
the agent registry stores roles, states and task descriptions; neither has a
field a credential could occupy. `forge.research` imports nothing from
`forge.vault`, `forge.propdesk.credentials` or the settings store.

## 19. Tests

**134 new Python tests** across ten modules (counts are what pytest collects):

| Module | Tests | What they pin |
| --- | --- | --- |
| `research/test_runtime.py` | 21 | A stalled engine never reads RUNNING; each stall kind; backoff. |
| `research/test_agents.py` | 18 | Roles; leases; replicas; honest capacity; restart. |
| `workstation/test_sidebar.py` | 18 | The catalogue; composition; bounds; round-trip. |
| `research/test_skips.py` | 14 | The hierarchy; useful vs wasted; durability; collapse. |
| `research/test_orchestration.py` | 13 | Allocation; health; no starvation; stall naming. |
| `api/test_sidebar_api.py` | 12 | Composition, editing and persistence over HTTP. |
| `api/test_multi_campaign.py` | 10 | Two campaigns end to end, through the real engine. |
| `api/test_engine_runtime.py` | 9 | Per-kind accounting; the ledger; the watchdog. |
| `research/test_boundaries.py` | 8 | What the research layer cannot import or construct. |
| `api/test_campaign_lifecycle_api.py` | 11 | Create, duplicate, archive, prioritise, export, deploy — over HTTP. |

Plus two added to `tests/api/test_director.py`: one pinning that a completed
campaign keeps saying so, and one pinning that a family proposal the gate refuses
is still researched at the level the gate assigned it.

Existing tests changed, each because the behaviour it pinned was deliberately
replaced: `test_campaign.py` (single-campaign rule removed), `test_director.py`
(fallback-to-random removed), `test_mcp_server.py` (two new read-only tools),
`App.test.tsx` (Switch now opens the switcher).

## 20. Visual QA

Four widths (1920 / 1440 / 1280 / 420) against a live API with two running
campaigns and five agents, then 42 campaigns under load. No console errors, no
page errors, no horizontal overflow at any width. Three real bugs found and
fixed — see the UX report.

## 21. Performance

`/campaigns/control-center` 11 ms / 64 KB with 42 campaigns; 42 cards rendered in
882 ms; a click handled in 207 ms while rendering. The loop backs off when
nothing can progress.

## 22. Recovery behaviour

Verified by `test_campaigns_agents_and_skips_all_survive_a_restart`: a second
process against the same workspace restores the campaign, its status, its agents
and their states, the skip ledger and the experiment claims, and resumes serving
the campaign without recreating it.

On start-up the control layer stops campaigns left running by a dead process,
stops their agents, and releases every lapsed claim.

## 23. Known limitations

- **Scheduler health is in memory.** A restart rebuilds it over one 60-cycle
  window. Deliberate: it is a short-term observation, not a durable fact about
  the research.
- **Agent roles bias, they do not execute.** An agent's role tilts which bucket
  the director draws from; it does not run a different algorithm. The role
  contracts in `forge.agents.skills` remain a separate, LLM-backed system.
- **Web research is retrieval-only** and gated on the campaign's `web_research`
  flag; literature findings enter the source store and never `JudgeInput`.
- **Sidebar reordering within a group** is exposed by the API and the model
  (both take a `position`) but is not offered in the rail: moving between groups
  is a button, and reordering within one waits for drag-and-drop. A grip icon was
  drafted and then removed — an affordance with a grab cursor and no handler
  behind it is a control that lies.
- **No live broker connector exists.** Unchanged from the baseline and correctly
  reported everywhere.

## 24. Remaining production work

1. Drag-and-drop reordering in the rail (the API and the model already support it).
2. Virtualised lists in the Control Center beyond a few hundred campaigns; at 42
   the flat grid is comfortable.
3. Persisting scheduler health across restarts, if long unattended runs show the
   first window costing real allocation quality.
4. Per-agent compute budgets are modelled and enforced but not yet surfaced in
   the deploy dialog.

## 25. Exact files changed

**New (25):**

```
packages/forge/research/runtime.py          packages/forge/research/skips.py
packages/forge/research/agents.py           packages/forge/research/orchestration.py
packages/forge/workstation/sidebar.py
apps/web/src/components/RuntimeState.tsx    apps/web/src/components/Sidebar.tsx
apps/web/src/components/WorkspaceSwitcher.tsx
apps/web/src/views/ResearchControl.tsx
apps/web/src/research.ts                    apps/web/src/workspaces.ts
apps/web/src/styles/runtime.css             apps/web/src/styles/sidebar.css
apps/web/src/styles/research-control.css
tests/research/test_runtime.py              tests/research/test_skips.py
tests/research/test_agents.py               tests/research/test_orchestration.py
tests/research/test_boundaries.py
tests/api/test_engine_runtime.py            tests/api/test_multi_campaign.py
tests/api/test_sidebar_api.py               tests/workstation/test_sidebar.py
docs/FULL_SYSTEM_AUDIT.md
```

**Modified (20):**

```
apps/api/forge_api/engine.py                apps/api/forge_api/director.py
apps/api/forge_api/experiments.py           apps/api/forge_api/campaigns.py
apps/api/forge_api/control.py               apps/api/forge_api/actions.py
packages/forge/research/campaign.py         packages/forge/research/__init__.py
packages/forge/workstation/models.py        packages/forge/workstation/store.py
packages/forge/modes/models.py
apps/web/src/App.tsx                        apps/web/src/App.test.tsx
apps/web/src/types.ts                       apps/web/src/main.tsx
apps/web/src/views/Pipeline.tsx             apps/web/src/components/icons.ts
tests/research/test_campaign.py             tests/api/test_director.py
tests/api/test_mcp_server.py
```

Plus this report and six other documents under `docs/`.

## 26. What "complete" means here

Not that every route exists. The acceptance criteria this was measured against:

- The 1,290 figure is **explained** — it was a sum of five unrelated situations,
  two of which saved no compute — and **replaced** by an accounting whose every
  number is backed by rows naming what the proposal collided with.
- `RUNNING` is now an invariant enforced in one place, and a live engine was
  observed reporting RUNNING while working and EXHAUSTED when its campaigns
  reached their budgets.
- Two campaigns were run concurrently through the real engine, each with its own
  crew, journal, frontier and claims, and both survived a process restart.
- A workspace holding destinations from four modes was composed through the same
  action registry the assistant uses, and rendered.
- G0–G13 is byte-identical to the baseline, and a test now asserts the ladder's
  shape so a gate cannot be quietly added, renamed or removed.
