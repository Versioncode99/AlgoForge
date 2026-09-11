# Implementation Progress

Kept current as the work proceeds so an interrupted session can resume without
re-deriving anything. Newest phase last.

## Phase 1 — Repository audit ✅

`docs/FULL_SYSTEM_AUDIT.md`. Traced the runtime, the skip counter and every
place the architecture assumes "one". Baseline test suite: green (1,750 tests).

## Phase 2 — Truthful runtime state, heartbeats, watchdog ✅

**New:** `packages/forge/research/runtime.py`

- `RuntimeState` — 13 explicit states (STARTING, RUNNING, PAUSED, IDLE,
  WAITING_FOR_WORK, WAITING_FOR_DATA, WAITING_FOR_AGENT, BLOCKED, EXHAUSTED,
  STOPPING, STOPPED, ERROR, RECOVERING).
- `Outcome` — what one cycle produced. Only `PROGRESS` resets the no-progress
  clock.
- `WorkerHeartbeat` — per-worker beat, progress mark, claim, stale/dead lease.
- `RuntimeMonitor.diagnose()` — the watchdog. Returns a state, a reason, a
  machine code and a remedy. **Cannot return RUNNING unless a worker made
  progress inside the window.**

**Changed:** `apps/api/forge_api/engine.py`

- `_cycle` now returns `(Outcome, reason)` from all seven terminal paths.
- `_loop` records each outcome; worker 0 runs `_watchdog()` each cycle.
- `_stage` doubles as the heartbeat.
- `status()` computes the diagnosis on read and exposes `runtime`, `working`.
- New `diagnose()` and `GET /engine/diagnostics`.

**Changed:** `apps/api/forge_api/director.py`

- A campaign that hits a stopping criterion now detaches with `exhausted=True`
  and `next_candidate` keeps returning `CAMPAIGN_EXHAUSTED` instead of `None`.
  Previously it returned `None`, the engine read that as "no campaign" and fell
  back to the original uniform random template draw — a silent mode change that
  kept the interface saying RUNNING.

## Phase 3 — Novelty hierarchy and explainable skip ledger ✅

**New:** `packages/forge/research/skips.py`

- `NoveltyLevel` — EXACT_DUPLICATE → NEAR_DUPLICATE → SAME_CONSTRUCTION →
  SAME_MECHANISM → RELATED_HYPOTHESIS → NOVEL_HYPOTHESIS → NOVEL_MECHANISM →
  NOVEL_FEATURE, with `rank`, `admits(floor=…)` and `level_from_score`.
  **`SAME_CONSTRUCTION` is deliberately not refused by default** — refining
  parameters inside a known construction is ordinary research, bounded by the
  budget rather than by the duplicate gate.
- `retry_rule` — only `EXACT_DUPLICATE` is never retryable; every other band
  carries the condition under which a retry is permitted.
- `SkipKind` — 9 kinds, split into `SAVED_COMPUTE` (declined a real experiment)
  and `WASTED` (refused nothing, because there was nothing to refuse).
- `SkipLedger` — durable SQLite record, one row per distinct refusal with an
  occurrence count, the matched prior object, the similarity and the retry rule.

**Changed:** `director.py` — all 12 `Refusal` sites now carry `kind`, `level`,
`matched`, `similarity`, `subject`.

**Changed:** `engine.py` — `skipped_by_memory` replaced by seven counters
(`skipped_duplicate`, `skipped_by_region`, `skipped_not_novel`,
`skipped_no_work`, `skipped_blocked`, `skipped_exhausted`, `skipped_errors`).
`skipped_by_memory` is retained as a derived value meaning only the three that
actually declined research; `compute_saved` no longer counts empty-frontier
cycles.

**Frontend:** `components/RuntimeState.tsx` (badge, diagnostics panel, skip
accounting), `styles/runtime.css`, wired into `views/Pipeline.tsx`; new types
in `types.ts`.

**Tests:** `tests/research/test_runtime.py` (16), `tests/research/test_skips.py`
(14), `tests/api/test_engine_runtime.py` (9).

## Next

Phase 4 — multi-campaign research fabric.

## Phase 4 — Multi-campaign research fabric ✅

**The constraint that was removed, and why it was safe to remove it.**
`CampaignStore.set_status` refused a second running campaign on the stated
grounds that two would "consume each other's burn-once holdout". That was not
true of the code: `ResearchLedger` is keyed by *strategy lineage*, and two
campaigns produce different strategies and therefore different lineages. What
they genuinely shared was the duplicate-claim namespace in `Experiments`.

- `Experiments` gains a `campaign_id` **column** and includes it in the claim
  identity when set. Two campaigns can now ask the same question and each
  establish its own evidence.
- The **trial count is deliberately not partitioned**: `Experiments.count(scope)`
  stays dataset-wide, because every trial run against this series is a trial
  whoever ran it. Narrowing it per campaign would lower the Deflated Sharpe's
  best-of-N hurdle exactly as more campaigns searched the same data.
- `CampaignStore.running()` replaces `active()`; `duplicate`, `archive`,
  `restore`, `prioritise`, `rename`, `set_agent_target` added. A duplicate
  copies **configuration only** — never progress, never findings.
- `ResearchDirector` is now one-per-engine serving **any number** of campaigns.
  Per-campaign state (`allocation`, `topics`, adapt cursor) moved into
  `_CampaignState`; the campaign a thread is serving is a **thread-local**, so
  forty journal writes record against the right campaign without being passed it.
- **Resumability bug found and fixed by the restart test:** `_catalog_version`
  hashed *all* of `TEMPLATES`, which the director adds to as it composes. Every
  restart after a campaign generated a template therefore started a fresh
  experiment scope and re-ran everything. It now hashes `SHIPPED_TEMPLATE_KEYS`.

**New:** `packages/forge/research/orchestration.py` — `ResearchOrchestrator`
deals workers to campaigns by `priority * health`, where health is observed
rather than declared. Every running campaign keeps at least one worker, because
a campaign starved to zero can never demonstrate that it recovered.

## Phase 5 — Multi-agent roles, leases and coordination ✅

**New:** `packages/forge/research/agents.py`

- `AgentRole` — 10 roles (DISCOVERY, LITERATURE, FEATURE, HYPOTHESIS,
  FALSIFICATION, REGIME, ROBUSTNESS, VALIDATION, REVIEWER, SPECIALIST), each
  with a stated purpose and preferred allocation buckets.
- `AgentState` — 9 states; `ResearchAgent` persisted with heartbeat, progress
  mark, claim, compute budget and error count.
- `AgentRegistry.claim` — leases with an expiry, so an agent that dies releases
  its work without a process restart. A deliberate replica is a separate claim
  with `replica_of` set, so "we ran it twice" can never read as two findings.
- `capacity_for` — honest capacity. Asking for 32 on a 4-core machine returns 8
  and a sentence explaining it, rather than 32 rows that never do anything.
- Roles **bias** the bucket draw and can never veto one: four falsification
  agents must not silently mean a campaign that never proposes anything.

## Phase 6 — Composable workspaces and custom sidebar ✅

**New:** `packages/forge/workstation/sidebar.py`

- `CATALOGUE` — the **union** of every destination any mode offers (49 of them),
  derived from the mode manifests rather than written again.
- `Sidebar` / `SidebarGroup` / `SidebarItem` with add, remove, move, reorder,
  rename, group, collapse, pin, hide.
- `default_sidebar_for(mode)` — the four built-in rails, unchanged and now
  editable.
- **Bug found by the bounds test:** every mutation used `model_copy`, which in
  pydantic v2 does **not** re-run field validators. The duplicate-route check
  and both ceilings only ran at construction, so a sidebar could be edited into
  a shape it could not have been created in. All mutations now rebuild.

`Workspace` gains `description`, `icon`, `kind` (BUILT_IN/USER_CREATED/CLONED),
`sidebar`, `pinned`, `campaign_ids`, `account_ids`, `mode` — migrated by
ALTER TABLE, so existing arrangements survive. `rail()` falls back to the mode's
sidebar, which is how a workspace saved before this still opens with navigation.

18 new actions in the **shared** registry (`list_sidebar_destinations`,
`add_sidebar_item`, …, `link_campaign_to_workspace`, `pin_workspace`) plus their
HTTP routes. There is deliberately no AI-only path.

## Phase 7 — Research Control Center and agent monitor ✅

- `GET /campaigns/control-center` — campaigns, allocation, agents, skips,
  validation accounting and frontier totals in one payload.
- `GET /campaigns/{id}/agents`, `POST` to deploy, `DELETE` to remove.
- `GET /campaigns/{id}/skips` — the drill-down behind the headline number.
- `GET /engine/diagnostics` — "why isn't my research running?" without a terminal.
- Frontend: `views/ResearchControl.tsx`, `research.ts`, `workspaces.ts`,
  `components/Sidebar.tsx`, `components/WorkspaceSwitcher.tsx`,
  `components/RuntimeState.tsx` and three stylesheets.

## Phase 8 — Tests, integration campaign, visual QA, documentation ✅

- **123 new Python tests** across nine modules, plus two added to the director's.
- **Integration:** two campaigns driven end to end through the real engine,
  synchronously, with their own crews, journals, frontiers and claims; both
  survive a process restart and resume.
- **Live verification:** a real API and engine ran two campaigns to their budgets
  with five agents. Worker allocation followed priority and health; the state
  read RUNNING while working and EXHAUSTED when the budgets were reached, with
  the reason in words.
- **Visual QA** at 1920/1440/1280/420 against that live system, and again with 42
  campaigns. No console errors, no page errors, no horizontal overflow. Three
  real bugs found and fixed (picker column order, search-icon specificity, a
  switcher that closed itself on background route corrections).
- **Performance:** control-center 11 ms / 64 KB at 42 campaigns; 42 cards in
  882 ms; click handled in 207 ms. Added `RuntimeMonitor.backoff` after load
  testing showed 768 cycles of which 749 were barren.
- **Boundary tests** assert what the research layer cannot import or construct,
  and that the G0–G13 ladder still has exactly fourteen gates.
- **Docs:** FULL_SYSTEM_AUDIT, AUTONOMOUS_RESEARCH_ARCHITECTURE,
  MULTI_CAMPAIGN_AGENT_ARCHITECTURE, WORKSPACE_ARCHITECTURE,
  PROP_DESK_ARCHITECTURE, RISK_AUTOMATION_ARCHITECTURE, UX_IMPLEMENTATION_REPORT,
  FINAL_IMPLEMENTATION_REPORT.

## Status

Complete.
