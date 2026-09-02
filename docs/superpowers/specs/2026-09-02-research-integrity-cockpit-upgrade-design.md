# AlgoForge Research Integrity & Cockpit Upgrade

**Date:** 2026-09-02  
**Status:** Approved from the operator's detailed brief and supplied visual references  
**Scope:** Local, paper-only research application. No broker order route.

## Outcome

This upgrade closes the two most dangerous gaps in the current vertical slice: real market data is incorrectly labelled out-of-sample, and the application can display attractive prop-firm probabilities without enough surrounding tail-risk context. It also adds a server-only OmniRoute model gateway, an honest NautilusTrader comparison boundary, and a denser research cockpit based on the operator's supplied screens.

The product remains a research desk, not a trading bot. Model output can propose hypotheses, code and post-mortems. Only deterministic services may calculate metrics, consume holdouts, issue verdicts or change promotion state.

## 1. Evidence lifecycle

Every real-data research run uses a deterministic chronological split:

1. **Development (60%)** — parameter search and debugging. Always labelled `DEVELOPMENT_IN_SAMPLE`.
2. **Purged gap** — at least the strategy warm-up length. No bar in this gap may enter either adjacent result.
3. **Validation (20%)** — first honest OOS measurement. Labelled `VALIDATION_OOS`.
4. **Purged gap** — same isolation rule.
5. **Holdout (remaining bars)** — hidden from normal iteration and consumed once per strategy lineage. Labelled `HOLDOUT`.

The split receipt contains source-data hash, partition ranges, timestamps, bar counts, purge size and a stable receipt ID. The judge may evaluate validation results, but the product must not show an overall PASS until validation passes. The holdout is only consumed after validation passes and must never be a diagnostic. A second attempt by the same lineage returns `HOLDOUT_ALREADY_CONSUMED`.

Legacy real-data artifacts remain readable but are relabelled `LEGACY_IN_SAMPLE`; real data alone never implies OOS. Synthetic and sweep results remain non-promotable.

## 2. Research diversity and HFT honesty

Templates gain explicit capability metadata: research family, bar/tick/depth requirement, minimum timeframe, session assumption and runnable/locked state. The current bar engine may run price/volume/session strategies. It may not claim to test queue position, market making, order-book imbalance, latency arbitrage or cross-venue execution from one-minute bars.

The catalog therefore has two lanes:

- **Runnable now:** breakout, mean reversion, volatility regime, session VWAP, opening range, liquidity sweep, volatility compression/expansion, volume-confirmed continuation and trend pullback.
- **Research locked:** L1 spread models, L2 imbalance, L3 queue models, cross-venue statistical arbitrage and sub-second market making. Each card states the missing dataset and engine capability.

`LINEAGE_RETIRED` is a research outcome: twelve parameter variants failed and the search budget closed. `ENGINE_ERROR` is a software incident. They must be counted and displayed separately.

## 3. Prop and risk analytics

The existing 30-day minimum, five-day block bootstrap and sample-aware interval remain mandatory. The simulator adds deterministic analytics over every simulated path:

- pass/fail/timeout and sample-aware interval;
- risk of ruin and first-boundary probabilities;
- target-reach cumulative curve by day;
- days-to-pass/fail quantiles;
- terminal outcome histogram;
- return versus maximum-drawdown points;
- 95% VaR and CVaR of terminal P&L;
- skewness and excess kurtosis;
- median, 5th and 95th percentile terminal outcomes.

Challenge and funded phases remain separate. Daily P&L cannot prove intraday trailing-drawdown compliance, so simulations based on daily bars carry `DAILY_SETTLEMENT_APPROXIMATION`. Every headline exposes seed, path count, sample days, block length, rule version and interval method.

## 4. OmniRoute gateway

AlgoForge uses OmniRoute through its OpenAI-compatible local API, defaulting to `http://127.0.0.1:20128/v1`. The API supports dynamic `/models` discovery and role routes such as `auto`, `auto/coding`, `auto/fast`, `auto/cheap`, `auto/smart`, `auto/offline` and `auto/lkgp`.

Secrets remain server-side. AlgoForge may read `OMNIROUTE_API_KEY`, `OMNIROUTE_KEY_FILE`, or the operator's conventional Desktop key file. The parser accepts only an explicitly labelled API key/token; a dashboard password is never used as a bearer token. Settings responses expose presence and source category only, never values, prefixes or filesystem paths. Connection tests return status, latency and model count. If OmniRoute is down, the console falls back to the deterministic local ledger and says why.

The current local probe reports connection refused, so the integration must ship in a truthful `configured/offline` state rather than fabricating connectivity.

## 5. NautilusTrader boundary

NautilusTrader is an optional execution-semantics oracle, not a replacement judge. Its Rust-native event-driven engine, deterministic time model and matching semantics make it useful for parity fixtures. Its LGPL-3.0 licence requires a deliberate dependency boundary and notices.

AlgoForge first ships a capability probe and adapter contract. The oracle reports installed/version/available data levels. It must refuse L2/L3 comparisons when only bars exist. No Nautilus package is auto-installed during this upgrade; installation and data mapping are a separate reviewed dependency decision.

Primary references:

- https://github.com/nautechsystems/nautilus_trader
- https://nautilustrader.io/docs/latest/concepts/backtesting/data-and-venues/
- https://nautilustrader.io/docs/latest/concepts/backtesting/execution-flow/
- https://github.com/diegosouzapw/OmniRoute
- https://quantpad.ai/

## 6. Research cockpit

The existing industrial dark system is retained. The supplied screens inform density and hierarchy, not pixel copying. The cockpit adds:

- a family/variant grid with honest medians, positive-share and gate status;
- a Forge Lab matrix of strategy versus market with explicit `not tested` cells;
- a strategy construction surface showing instrument, session, HTF context, confirmation and entry layers;
- an evidence ribbon on every result: `IS`, `OOS`, `HOLDOUT`, `FORWARD`, `LEGACY`;
- Prop sub-tabs for Challenge/Funded, Verdict, Regimes, and Risk & Monte Carlo;
- interactive equity paths, boundary race, terminal distribution and return/drawdown map;
- settings for provider status, role routing, budgets, oracle capabilities and secret-safe credentials.

The app must remain keyboard usable, responsive at 360/768/1440 widths, and respect reduced motion. Loading, empty, locked, insufficient-sample, offline and error states are first-class.

## Acceptance criteria

1. No code path maps `REAL_DATA` directly to `TRUTH_OOS`.
2. Split ranges are chronological, purged, non-overlapping and content-addressed.
3. A holdout lineage can be consumed exactly once.
4. Prop analytics are deterministic at a fixed seed and refuse fewer than 30 observed days.
5. HFT cards are locked unless their stated data capability exists.
6. OmniRoute credentials never appear in API payloads, logs, artifacts, tests or Git history.
7. OmniRoute offline state falls back locally without breaking the console.
8. Nautilus capability reports are accurate and cannot be mistaken for completed calibration.
9. Python tests, ruff, strict mypy, Vitest, Vite build and Playwright pass.
10. The upgraded core flow is clicked through in a real browser and archived in vault evidence.
