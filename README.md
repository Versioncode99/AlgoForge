# AlgoForge

AlgoForge is a private, local, paper-only quantitative research workstation. It creates immutable research runs, deterministic verdicts, ordinary risk analysis, versioned prop-firm simulations, bounded research-agent projections, and guarded application-maintenance evidence.

Current state: working local vertical slice, implemented in ten Git-tracked waves. All bundled market and strategy output is sample data and uncalibrated. No live-order, account-signup, payment, KYC, or production-broker capability exists.

## Development prerequisites

- Windows with PowerShell
- Python 3.13 via `uv`
- Node.js 22-24 and npm
- Git and GitHub CLI

## Start locally

```powershell
uv sync --all-groups
npm install --prefix apps/web
.\scripts\Start-AlgoForge.ps1
```

Open `http://127.0.0.1:5173`. The API documentation is at `http://127.0.0.1:8765/docs`.

## Verify

```powershell
.\scripts\Test-AlgoForge.ps1
```

The workstation includes immutable run contracts, point-in-time data validation, deterministic evidence gates, fixed-seed risk paths, separately versioned challenge and funded account rules, cited agent dissent, and a human-gated ForgeKeeper release supervisor. Architecture and the executable plan are in `docs/superpowers/`.
