# AlgoForge Local Research Platform Design

Status: approved for implementation by the user's 2026-09-01 instruction to build from the Keslec-Trading architecture. Confidence tags in this document describe evidence status, not investment confidence.

## Outcome

AlgoForge is a private, paper-only quantitative research workstation running from `F:\AlgoForge`. It turns frozen hypotheses and point-in-time market data into immutable runs, deterministic verdicts, ordinary risk analysis, and versioned prop-firm simulations. It also includes ForgeKeeper, an application-maintenance and competitive-research agent that may propose and test changes across the application while a protected Recovery Supervisor alone controls release activation and rollback.

The first deliverable is a functional local vertical slice, not a claim that every future provider, execution oracle, or autonomous model integration is complete. Sample datasets and simulated outcomes are labelled. No live order path exists.

## Considered approaches

1. A Python-only dashboard would reduce initial tooling but weaken the high-density, interactive chart and state-management requirements.
2. A TypeScript-only platform would simplify contracts but make statistical research and compatibility with the vault's Python quant assets unnecessarily difficult.
3. A Python research/API core with a React TypeScript frontend gives the cleanest boundary. This is selected. FastAPI publishes OpenAPI; React consumes generated types; deterministic algorithms remain below the API projection.

For storage, the production architecture retains Postgres plus Parquet/DuckDB. The local vertical slice begins with SQLite metadata and JSON/Parquet-compatible artifacts behind repository interfaces, allowing Postgres to replace SQLite without changing domain services. This keeps installation reliable on Windows while preserving the target contracts.

## Repository shape

```text
apps/api/                 FastAPI projection and dependency wiring
apps/web/                 React, Vite, TypeScript interface
packages/forge/           Python domain packages
  contracts/              identifiers, envelopes, schemas
  ledger/                 immutable runs, references, decisions
  data/                   provider contracts and validation
  sweep/                  owned deterministic sweep engine
  judge/                  deterministic G0-G9-style verdicts
  prop/                   versioned challenge/funded state machines
  agents/                 bounded research roles and memory
  forgekeeper/            incidents, scouting, patch/release policy
services/recovery-supervisor/  protected release-pointer authority
rules/                    versioned executable prop contracts
fixtures/                 deterministic sample data and incidents
tests/                    unit, contract, integration, and E2E evidence
docs/                     architecture, plans, research, receipts
```

## Authority model

The judge is deterministic and cannot import model clients, agent packages, web code, or prop-optimisation code. Every run references a frozen preregistration hash, data hash, code hash, cost-model hash, and engine version. In-sample, sweep, truth/OOS, holdout, and forward tiers are distinct. A prop simulation accepts only signed truth/OOS or forward artifacts and cannot change the underlying verdict or promotion state.

Every external code or design candidate enters a reference ledger with an exact commit, licence evidence, adoption lane, source path, decision, and removal seam. MIT and Apache material may be adopted with notices. AGPL components remain isolated with source obligations. Missing-licence repositories are behaviour references only and require clean-room implementation.

## Functional vertical slice

The initial runnable program provides:

- a health and overview API;
- immutable demo run creation and retrieval;
- a deterministic verdict with gate findings and calculation traces;
- regime attribution and ordinary Monte Carlo paths;
- separate Topstep-style and Lucid-style 50K challenge/funded rule contracts;
- deterministic prop simulation with pass, fail, timeout, confidence interval, boundary events, and equity paths;
- a dark instrument-panel frontend with Overview, Verdict, Regimes, Risk and Monte Carlo, Prop Challenge, Prop Funded, Rules, Agents, and Evolution views;
- a bounded research-agent status surface;
- ForgeKeeper incident, repository-candidate, release-gate, and rollback projections;
- local start scripts, tests, CI, provenance docs, and a private GitHub repository.

The default rule fixtures model the user-requested common shape: 50,000 starting balance, 3,000 target, and 2,000 maximum-loss allowance. They remain explicitly illustrative until refreshed against current official source snapshots; provider-specific differences are never collapsed into one generic preset.

## Data flow

An operator selects or creates a frozen study input. The application core validates chronology and creates an immutable run. The sweep layer explores parameters and records every trial but has no promotion authority. The truth fixture emits deterministic trades. The judge evaluates signed inputs and emits a versioned verdict. Normal Analysis reads those artifacts. Prop Lab wraps the same accepted trade stream in a selected rule-state machine and stores a separate simulation artifact. The frontend reads projections only and opens every headline result into a calculation trace.

ForgeKeeper ingests an incident or repository delta into quarantine, creates a bounded candidate, records tests and licence evidence, and produces a release proposal. It cannot write the protected supervisor or activate a release. The supervisor verifies signed manifests, performs health checks, and owns atomic promote/rollback pointers.

## Failure handling and safety

Malformed data, missing preregistration, expired rules, missing intraday marks, unknown licences, infrastructure failures, skipped tests, and non-finite metrics fail closed. Identical content and seeds reproduce identical identifiers and artifacts. API errors use typed envelopes without stack traces. Mutations are local-only and auditable. No secret is committed. Testnet integrations require human-provisioned credentials. Live brokers, payments, KYC, prop signups, and order placement are out of scope.

## Verification

Each wave ends with a commit and executable evidence. Unit tests cover hashes, chronology, judge determinism, state-machine boundaries, confidence intervals, and forbidden imports. Contract tests cover API envelopes and rule expiry. Integration tests prove that prop results cannot mutate verdicts. Playwright verifies keyboard navigation, responsive layouts, click depth, no console errors, and the core golden journey. CI runs on Windows and Linux where practical. Final evidence includes screenshots, test logs, an SBOM, third-party notices, repository URL, and vault close-out records.

## Scope control

The ten waves produce a trustworthy local research platform and extensible seams. Real Databento downloads, NinjaTrader Strategy Analyzer calibration, unattended multi-day operation, paid model credentials, and live external services require user-provided data or credentials and remain separately gated. The program must show these as unavailable or unverified, never fabricate completion.
