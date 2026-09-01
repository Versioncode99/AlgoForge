# AlgoForge Ten-Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working, private, paper-only AlgoForge research platform on the F drive with deterministic analytics, prop simulations, agent observability, and guarded self-maintenance.

**Architecture:** A Python FastAPI and domain core owns research truth, persistence, simulations, and maintenance policy. A React/Vite TypeScript client is a read-mostly projection. SQLite is the first metadata adapter; repository protocols preserve the production Postgres/Parquet migration seam.

**Tech Stack:** Python 3.13, uv, FastAPI, Pydantic v2, SQLModel/SQLite, NumPy, SciPy, React 19, TypeScript, Vite, TanStack Query, ECharts, Vitest, pytest, Playwright, GitHub Actions.

---

## Wave 1: Foundation, source audit, and governance

**Files:** `pyproject.toml`, `package.json`, `.github/workflows/ci.yml`, `docs/research/*`, `prompts/waves/*`, `THIRD_PARTY_NOTICES.md`, `apps/*/README.md`.

- [ ] Write tests that assert the package layout, paper-only capability manifest, and absence of live-order endpoints.
- [ ] Add Python and Node workspace manifests with pinned compatibility ranges and committed lockfiles.
- [ ] Add the reference/licence ledger seed for every audited repository at its fixed commit.
- [ ] Add ten separate exactly 250-word wave prompts and a deterministic word-count check.
- [ ] Run foundation checks; expected result is zero unlicensed copied assets and no live-trading capability.
- [ ] Commit as `chore: establish AlgoForge research foundation`.

## Wave 2: Contracts, persistence, and command core

**Files:** `packages/forge/contracts/*.py`, `packages/forge/ledger/*.py`, `apps/api/forge_api/main.py`, `tests/contracts/*`, `tests/ledger/*`.

- [ ] Write failing tests for canonical hashing, typed identifiers, command envelopes, preregistration chronology, immutable runs, and decision hash chains.
- [ ] Implement Pydantic contracts and a SQLite repository behind protocols.
- [ ] Add `/api/v1/health`, `/overview`, `/runs`, `/verdicts`, and trace resources using consistent envelopes.
- [ ] Prove an attempted mutation of a finalized run fails and hash-chain tampering is detected.
- [ ] Commit as `feat: add immutable research contracts and ledger`.

## Wave 3: Deterministic data spine

**Files:** `packages/forge/data/*.py`, `fixtures/market/*.json`, `tests/data/*`.

- [ ] Write failing tests for timestamp order, duplicates, non-finite values, knowledge time, session metadata, and provider provenance.
- [ ] Implement canonical bars, frozen queries, provider descriptors, validation receipts, and local fixture provider.
- [ ] Add explicit Databento, FRED, and crypto adapter interfaces without requiring secrets.
- [ ] Prove unavailable truth sources fail closed and cannot silently fall back to sample data.
- [ ] Commit as `feat: add point-in-time data contracts and validation`.

## Wave 4: Sweep, truth fixtures, and verdict judge

**Files:** `packages/forge/sweep/*.py`, `packages/forge/judge/*.py`, `fixtures/strategies/*`, `tests/judge/*`.

- [ ] Write failing tests for trial accounting, deterministic order generation, no-lookahead detection, noise rejection, multiple-testing penalties, and forbidden imports.
- [ ] Implement the owned array sweep, signed truth fixture, gate ladder, dimension scores, grade projection, and calculation traces.
- [ ] Keep sweep output exploratory and require signed truth/OOS input for a promotable verdict.
- [ ] Prove identical inputs reproduce identifiers and injected lookahead fails.
- [ ] Commit as `feat: implement deterministic strategy judge`.

## Wave 5: Normal Analysis

**Files:** `packages/forge/analytics/*.py`, `apps/api/forge_api/routes/analysis.py`, `tests/analytics/*`.

- [ ] Write failing tests for regime attribution, transition matrices, stationary-block bootstrap, drawdown, VaR/CVaR, recovery, and interval calculation.
- [ ] Implement deterministic simulations with fixed seeds and calculation trace resources.
- [ ] Add Verdict, Regimes, and Risk/Monte Carlo API projections.
- [ ] Prove sparse regimes and inadequate tails produce visible warnings instead of invented estimates.
- [ ] Commit as `feat: add normal analysis and risk simulation`.

## Wave 6: Prop Lab

**Files:** `packages/forge/prop/*.py`, `rules/*.yaml`, `apps/api/forge_api/routes/prop.py`, `tests/prop/*`.

- [ ] Write hand-worked state-machine tests for target, loss floor, trailing/eod rules, daily stops, consistency extension, timeout, payout, deduction, and reset.
- [ ] Implement independent challenge and funded contracts plus immutable simulation specs and Wilson intervals.
- [ ] Reject sweep/IS input, expired rules, and missing intraday fidelity at the API boundary.
- [ ] Prove a prop result cannot alter a verdict or promotion record.
- [ ] Commit as `feat: implement versioned prop challenge and funded simulations`.

## Wave 7: Agents and memory

**Files:** `packages/forge/agents/*.py`, `packages/forge/memory/*.py`, `apps/api/forge_api/routes/agents.py`, `tests/agents/*`.

- [ ] Write failing tests for role permissions, frozen evidence packs, cited claims, dissent, budgets, holdout isolation, and memory authority.
- [ ] Implement deterministic stub roles, provider adapter interfaces, fact/constraint/skill/narrative partitions, and outcome-weighted retrieval.
- [ ] Add approvals and runtime event projections without giving agents release or trading authority.
- [ ] Prove narrative changes cannot change a judge result.
- [ ] Commit as `feat: add bounded research agents and memory`.

## Wave 8: ForgeKeeper and Recovery Supervisor

**Files:** `packages/forge/forgekeeper/*.py`, `services/recovery-supervisor/*`, `apps/api/forge_api/routes/evolution.py`, `tests/forgekeeper/*`.

- [ ] Write failing tests for incident reproduction, path manifests, licence lanes, candidate gates, protected paths, canary thresholds, and rollback.
- [ ] Implement incident, repository snapshot, feature candidate, release manifest, health comparison, and rollback contracts.
- [ ] Implement a minimal supervisor that owns active/previous release pointers and rejects unsigned or unhealthy candidates.
- [ ] Prove workers cannot mutate supervisor paths and forced canary failure restores last-known-good state.
- [ ] Commit as `feat: add guarded self-maintenance and repository scout`.

## Wave 9: Frontend product surface

**Files:** `apps/web/src/**/*`, `apps/web/tests/*`, `tests/e2e/*`.

- [ ] Write component tests for loading, empty, stale, failure, locked-rule, and populated states.
- [ ] Implement the instrument-panel shell, Overview, Analysis tabs, Prop Lab tabs, Agents, and Evolution views.
- [ ] Add keyboard-accessible controls, semantic colour plus non-colour labels, responsive layouts, and two-click trace drawers.
- [ ] Run Vitest and Playwright at 375, 768, and 1440 pixels; expected result is no console errors or horizontal overflow.
- [ ] Commit as `feat: deliver AlgoForge operator interface`.

## Wave 10: Integration, packaging, and evidence

**Files:** `scripts/*`, `docker-compose.yml`, `README.md`, `docs/evidence/*`, vault project/status/session notes.

- [ ] Run the golden journey from frozen hypothesis through truth verdict, Normal Analysis, prop replay, agent review, and ForgeKeeper incident.
- [ ] Run lint, type checking, unit, contract, integration, E2E, accessibility, security, and performance checks on Windows; run Linux CI in GitHub Actions.
- [ ] Generate screenshots, hashes, SBOM, notices, API schema, and a release receipt.
- [ ] Publish the private GitHub repository and verify the remote commit.
- [ ] Update the Keslec-Trading project, STATUS, index, canonical build report, and session log with confidence tags.
- [ ] Commit as `docs: close AlgoForge local vertical slice`.

## Self-review

The plan covers every approved subsystem, defines concrete paths, keeps agent and prop outputs outside judge authority, and contains no implementation placeholders. Type names are stable across waves. External credentials and real-platform calibration are explicit post-build gates, not fabricated acceptance evidence.
