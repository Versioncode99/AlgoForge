# Qanat-style Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use the applicable frontend,
> MCP, quant-validation, and browser-QA skills while executing these tasks in
> order. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair Agent Command and give AlgoForge a durable, observable,
agent-native research-to-validation control plane without replacing its engine.

**Architecture:** One typed action registry remains the only mutation boundary.
The React UI, orchestrator, lifecycle-owned research scheduler, and local MCP
adapter call it. Validation and prop simulations remain deterministic engine
services and gain read projections for the new visual surface.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, SQLite, official MCP Python SDK,
React 19, TypeScript, TanStack Query, ECharts, Vitest, Playwright.

---

### Task 1: Repair Agent Command against a growing role registry

**Files:**
- Modify: `apps/api/forge_api/agent_service.py`
- Modify: `apps/api/forge_api/control.py`
- Modify: `apps/web/src/views/AgentCommand.tsx`
- Create: `apps/web/src/components/ViewErrorBoundary.tsx`
- Modify: `apps/web/src/App.tsx`
- Test: `tests/agents/test_command.py`
- Test: `apps/web/tests/e2e/agent-command.spec.ts`

- [ ] Add an API contract with `orchestrator` and `specialists` as separate
  fields while retaining a combined task-history projection.
- [ ] Write an end-to-end assertion that the nine-role response renders eight
  specialist nodes, an orchestrator core, and no `pageerror`; verify it fails.
- [ ] Replace fixed `POSITIONS` destructuring with a polar-layout function:
  `specialistPosition(index, count) -> {left, top, controlX, controlY}`.
- [ ] Wrap lazy views in an error boundary with Retry and Overview actions.
- [ ] Run `pytest tests/agents/test_command.py -q`, Vitest, and the targeted
  Playwright test; expect all to pass.

### Task 2: Add the durable continuous research service

**Files:**
- Create: `apps/api/forge_api/research_loop.py`
- Modify: `apps/api/forge_api/settings_store.py`
- Modify: `apps/api/forge_api/main.py`
- Modify: `apps/api/forge_api/control.py`
- Modify: `apps/api/forge_api/actions.py`
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/views/Settings.tsx`
- Modify: `apps/web/src/views/AgentCommand.tsx`
- Create: `tests/api/test_research_loop.py`

- [ ] Test lifecycle start/stop, due-cycle calculation, deduplication, manual
  trigger, pause, backoff, and a model-call ceiling with fake time/network.
- [ ] Implement `ResearchLoop` with a stop event, one daemon thread, SQLite
  receipts, a bounded rotating agenda, and status/trigger/control methods.
- [ ] For new sources, dispatch `hypothesis` then `strategy_code`; allow family
  creation only through the existing guarded `create_family` action.
- [ ] Add `/research-loop`, `/research-loop/run`, and `/research-loop/settings`.
- [ ] Show cadence, last/next run, sources found, downstream tasks, errors, and
  pause/run-now controls in Agent Command and Settings.
- [ ] Verify an isolated cycle writes source and mission notes into the vault
  mirror while an unavailable network records backoff rather than killing API.

### Task 3: Make walk-forward and Monte Carlo inspectable

**Files:**
- Modify: `packages/forge/research/walkforward.py`
- Modify: `apps/api/forge_api/strategies.py`
- Modify: `apps/api/forge_api/control.py`
- Create: `apps/api/forge_api/validation_views.py`
- Create: `apps/web/src/views/ValidationLab.tsx`
- Create: `apps/web/src/styles/validation.css`
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/App.tsx`
- Test: `tests/api/test_validation_views.py`
- Test: `apps/web/src/App.test.tsx`
- Test: `apps/web/tests/e2e/validation-lab.spec.ts`

- [ ] Persist fold-level choices and per-fold Sharpe alongside the aggregate
  evidence without changing the judge’s current gate inputs.
- [ ] Add read-only overview/detail endpoints for validation evidence and saved
  prop simulations; return named absence reasons instead of zeros.
- [ ] Build fold, PBO, path-tail, target/loss, terminal, drawdown, and equity-path
  views with exact method/seed/path metadata.
- [ ] Add heatmaps for strategy-by-gate and strategy-by-rule; exclude derived
  blends from correlation-like comparisons.
- [ ] Test empty, stale, insufficient-day, and populated states; verify labels do
  not equate CPCV with Monte Carlo or simulated passing with edge.

### Task 4: Apply the Qanat-derived visual system and honest motion

**Files:**
- Modify: `apps/web/src/styles/tokens.css`
- Modify: `apps/web/src/styles/base.css`
- Modify: `apps/web/src/styles/app.css`
- Modify: `apps/web/src/styles/pipeline.css`
- Modify: `apps/web/src/styles/motion.css`
- Modify: `apps/web/src/views/Pipeline.tsx`
- Modify: `apps/web/DESIGN.md`

- [ ] Replace cool blue-black surfaces with the approved warm-black/lime token
  set while preserving semantic evidence and verdict colours.
- [ ] Make graph packets event-driven from activity timestamps and remove idle
  travel, ambient pulsing, and movement that does not encode work.
- [ ] Use drawers attached to selected graph nodes for configuration/details.
- [ ] Verify 1280 and 1920 pixel layouts, keyboard focus, contrast, overflow,
  reduced motion, and zero critical console/network errors.

### Task 5: Expose the action registry through local MCP

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `apps/api/forge_api/mcp_server.py`
- Modify: `apps/api/forge_api/actions.py`
- Modify: `README.md`
- Create: `tests/api/test_mcp_server.py`

- [ ] Add the official `mcp>=2,<3` dependency and `algoforge-mcp` script.
- [ ] Build one shared application context factory so API and MCP resolve the
  same storage pointer, action schemas, locks, ledgers, and vault mirror.
- [ ] Register one MCP tool per action with its description, input schema,
  structured receipt, and read/write annotation; default to read-only.
- [ ] Refuse every mutating tool unless the server launched with `--write`.
- [ ] Smoke initialize, list tools, call a read tool, assert mutations absent by
  default, then call a safe isolated mutation in `--write` mode.

### Task 6: Close verification, vault records, and GitHub delivery

**Files:**
- Modify: `README.md`
- Modify: `F:/Obsidian Vaults/AlgoForge-Vault/STATUS.md`
- Modify: `F:/Obsidian Vaults/AlgoForge-Vault/03 Projects/Trading/AlgoForge.md`
- Create: `F:/Obsidian Vaults/AlgoForge-Vault/05 Logs/Sessions/2026-09-07 AlgoForge Qanat control plane.md`
- Create: `F:/Obsidian Vaults/AlgoForge-Vault/08 Source Material/Trading/AlgoForge/evidence/2026-09-07-qanat-control-plane-evidence.md`

- [ ] Run `.venv/Scripts/python.exe -m ruff check .`, strict mypy, and full
  pytest; record exact counts and warnings.
- [ ] Run Vitest, Vite production build, and the desktop Playwright suites; record
  exact counts, sizes, screenshots, console errors, and any unpassed gate.
- [ ] Confirm storage/vault counts and verify new notes are discoverable.
- [ ] Review `git diff --check`, secrets, generated artifacts, and remote branch.
- [ ] Commit the reviewed work, push `feat/industry-standard-validation`, and
  verify the remote commit ID equals local HEAD.
