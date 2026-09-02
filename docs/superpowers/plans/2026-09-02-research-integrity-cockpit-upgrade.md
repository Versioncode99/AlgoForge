# AlgoForge Research Integrity & Cockpit — Implementation Plan

**Design:** `docs/superpowers/specs/2026-09-02-research-integrity-cockpit-upgrade-design.md`

## Wave 1 — Establish the integrity baseline

- Preserve the clean 75-pytest / 5-vitest baseline.
- Ignore local settings and add a public example configuration.
- Add evidence-tier constants and regression tests proving real data is not OOS.
- Reclassify legacy artifacts without deleting operator data.

## Wave 2 — Chronological split receipts

- Add immutable partition and split-receipt models.
- Implement deterministic 60/20/remaining chronological splits with warm-up purge gaps.
- Add unit tests for chronology, non-overlap, stable IDs, minimum sample and edge cases.

## Wave 3 — Burn-once holdout and judge wiring

- Add a persistent holdout-consumption ledger with a unique lineage constraint.
- Run development, validation and optional holdout artifacts separately.
- Make manual and autonomous judge paths use validation only.
- Expose split evidence and explicit lineage-retired versus engine-error counters.

## Wave 4 — Strategy capability catalog

- Enrich template metadata with data requirements and research status.
- Add bar-compatible families sourced from the operator's strategy library patterns.
- Add locked HFT/order-book/cross-venue research cards with exact missing capabilities.
- Add family/variant aggregation and Forge Lab matrix endpoints.

## Wave 5 — QuantPad-depth prop analytics

- Extend every simulation with risk of ruin, boundary race, target curve, terminal histogram, return/drawdown map, VaR, CVaR, skew and kurtosis.
- Compute from all paths before response truncation.
- Add fixed-seed tests and preserve the sample-aware interval.

## Wave 6 — OmniRoute model gateway

- Add an OpenAI-compatible provider protocol and OmniRoute client.
- Discover models dynamically with static `auto/*` fallbacks.
- Parse only explicit API-key labels; ignore dashboard passwords.
- Add status and connection-test endpoints plus console fallback.
- Add tests proving no credential value or path reaches API/log output.

## Wave 7 — Nautilus execution oracle boundary

- Add optional package/version capability probe.
- Define comparison receipt and data-level compatibility checks.
- Expose oracle status in API/settings.
- Document LGPL dependency boundary and defer installation.

## Wave 8 — Research cockpit UI

- Add Models/Research Lab view with family cards and capability locks.
- Add strategy/market heatmap and selected-strategy evidence context.
- Upgrade Prop Firm into Challenge/Funded, Verdict, Regimes and Risk/MC sub-tabs.
- Add boundary-race, terminal-distribution and return/drawdown charts.

## Wave 9 — Browser and regression verification

- Run ruff, strict mypy, pytest, Vitest and production build.
- Start the real API and web app, exercise core flows with Playwright.
- Check 1440, 768 and 360 widths, keyboard focus, console errors and reduced motion.
- Capture evidence screenshots.

## Wave 10 — Release and vault close-out

- Update README, third-party notices and architecture status.
- Commit coherent slices; push to the configured GitHub remote if authenticated.
- Update the canonical AlgoForge project note, STATUS and session log with confidence tags.
- State unbuilt gates plainly: NinjaTrader fill calibration, Nautilus parity run, L2/L3 datasets and real forward evidence.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest
npm run test:web
npm run build:web
npm run test:e2e
```

## Commit sequence

1. `docs: specify research integrity and cockpit upgrade`
2. `fix: enforce chronological OOS and burn-once holdout`
3. `feat: add strategy capabilities and prop tail analytics`
4. `feat: wire OmniRoute and Nautilus capability boundaries`
5. `feat: ship research cockpit analytics`
6. `docs: close out verified research cockpit upgrade`
