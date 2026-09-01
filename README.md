# AlgoForge

AlgoForge is a private, local, paper-only desktop application for building, running and judging trading strategies. It writes strategies to disk as real Python, backtests them on real market data, judges them through a deterministic gate ladder, and simulates how they would fare under funded-account rules.

**Market data is real.** CME futures come from Databento (cost-checked before each download; roughly $0.025 per week of MNQ 1-minute bars) and crypto from Binance public endpoints (free). A seeded, deliberately edge-free synthetic dataset is available for offline work and can never clear the judge's G0 data gate.

**Fills are modelled, not calibrated.** Backtests apply commission and ATR-proportional slippage, but they have not been reconciled against a live platform. Calibrate against NinjaTrader's Strategy Analyzer before trusting any number. No live-order, account-signup, payment, KYC, or production-broker capability exists.

## Development prerequisites

- Windows with PowerShell
- Python 3.13 via `uv`
- Node.js 22-24 and npm
- Git and GitHub CLI

## Start locally

```powershell
uv sync --all-groups
npm install --prefix apps/web
npm install --prefix apps/desktop
npm run build:web
.\scripts\Start-AlgoForge.ps1
```

`Start-AlgoForge.ps1` opens the desktop app, which owns the API: closing the
window shuts everything down. Pass `-Web` to run it in a browser instead.

## Strategies

Strategies live on disk under `strategies/<id>/` as three readable files: the
frozen `spec.json`, the `strategy.py` that actually executes, and a
`test_strategy.py` carrying a lookahead trap. Edit them by hand or from the
Strategies tab; either way they pass the same static guard, which refuses
filesystem, network, subprocess and dynamic-execution access before anything
runs. Generated strategies are gitignored — the templates that produce them, in
`packages/forge/strategy/templates.py`, are what is version-controlled.

## Autonomous engine

Overview has a start/stop control. Once running it draws parameters from each
template's declared ranges, writes the strategy, backtests it on the selected
dataset, judges it, and records the outcome. Failures become constraints that
skip matching candidates before any compute is spent. Expect most candidates to
be rejected: that is the system working, not failing.

Open `http://127.0.0.1:5173`. The API documentation is at `http://127.0.0.1:8765/docs`.

## Verify

```powershell
.\scripts\Test-AlgoForge.ps1
```

The workstation includes immutable run contracts, point-in-time data validation, deterministic evidence gates, fixed-seed risk paths, separately versioned challenge and funded account rules, cited agent dissent, and a human-gated ForgeKeeper release supervisor. Architecture and the executable plan are in `docs/superpowers/`.
