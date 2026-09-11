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
