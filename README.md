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

## Where output goes

Everything the application writes — strategy code, backtest artifacts, the
research library, the agent and mission ledgers — lives in a **workspace**, set
in Settings. It defaults to the repository, and pointing it at an Obsidian vault
splits the output in two: readable Markdown notes under `10 AlgoForge/`, and the
machine storage under `10 AlgoForge/.store/`, which Obsidian hides. One note is
written per strategy, paper, backtest, verdict, family and mission as the work
happens, so a run left going overnight is readable in the morning without opening
the app.

Three things stay with the code: `rules/` (version-controlled prop contracts),
`config/` (which holds the pointer to the workspace, so it cannot live inside
it), and `data/market/` (purchased vendor archives — relocating the workspace
must never cost a paid re-download).

The switch takes effect on restart. The running process keeps its open databases
where they are rather than splitting the ledger across two roots mid-run.

## Strategies

Strategies live on disk under `<workspace>/strategies/<id>/` as three readable
files: the frozen `spec.json`, the `strategy.py` that actually executes, and a
`test_strategy.py` carrying a lookahead trap. Edit them by hand or from the
Strategies tab; either way they pass the same static guard, which refuses
filesystem, network, subprocess and dynamic-execution access before anything
runs. Generated strategies are gitignored — the templates that produce them, in
`packages/forge/strategy/templates.py`, are what is version-controlled.

## Families and templates

Families are a registry, not an enum: twelve ship, and more can be added at run
time with a stated economic mechanism. A family declaring data the installed
providers cannot serve — an options chain, L2 depth — is registered **blocked**
and the strategy writer refuses it. Naming a family never conjures its data.

Templates can also be added at run time, on exactly the terms the shipped ones
meet: the source passes the static guard, declares a searchable parameter grid,
and is **executed on synthetic bars before it is registered**. A template that
raises never reaches the engine. Once accepted it is indistinguishable from a
built-in one at the point of use, and no more capable — it still sees only a
right-bounded window, so it still cannot look ahead.

## Orchestration

The Orchestrator tab takes an objective and produces a *plan*: an ordered list of
actions and specialist assignments, shown before anything runs and runnable
verbatim once read. Later steps read earlier results through `{{stepN.field}}`,
which is what turns "find a paper, build from it, measure it" into one
instruction instead of four.

The plan may only use verbs that already exist. `GET /api/v1/actions` lists them
with their schemas; the console assistant calls the same set, which is why it can
now search for papers, add a family or start a backtest instead of explaining
that it cannot. Every call is validated, logged, and refusable with a reason.

## Autonomous engine

Overview has a start/stop control. Once running it draws parameters from each
template's declared ranges, freezes the hypothesis it is about to test, writes
the strategy, backtests it on the selected dataset, judges it, and records the
outcome. Expect most candidates to be rejected: that is the system working, not
failing.

Failures are not discarded. Each one is recorded with a **class**, and the class
declares how far its evidence reaches — a parameter set that produced no trades
says something about its neighbours, a consumed holdout says nothing about
anything. Two gates run before any compute: one refuses a byte-identical repeat,
the other refuses a candidate sitting inside a region already disproven for a
reason that generalises. Both survive a restart.

Absent evidence is never learned from. The judge uses `INCONCLUSIVE` to mean
*never measured*, and pruning a region because nobody looked at it would delete
candidates on the strength of nothing.

Open `http://127.0.0.1:5173`. The API documentation is at `http://127.0.0.1:8765/docs`.

The **Pipeline** tab draws the whole graph — sources, catalogue, candidates,
measurement, judgement, survival — with live counts, and lights the stage each
worker is in.

## Evidence

The **Evidence** tab answers one question for a chosen candidate: why is this
trusted, or not. It shows the verdict and the full gate ladder with *failed* and
*never measured* kept apart, the lineage back to the experiment that produced
it, what research memory already knows about that template, the specialist
positions with their disagreement intact, and every limitation the judge
attached to a number.

Sections that have no data say so, with a reason. A candidate that was never
validated shows "validation has never been run", not a panel of zeroes — a zero
reads as a measurement.

The same document is available at `GET /api/v1/strategies/{id}/dossier` and as
the read-only `strategy_dossier` action, so the interface, an agent and MCP all
read one implementation.

## What the judge will not do

The gate ladder returns three answers, not two. `PASS`, `FAIL`, and
`INCONCLUSIVE` — the last meaning the evidence was never produced. A strategy
that was never walk-forwarded is an unanswered question, not a near miss.

Two gates refuse evidence that exists but is too thin to carry the claim. The
Deflated Sharpe needs the spread of the search to set its hurdle, and the
probability of backtest overfitting needs rivals to rank the winner against;
below eight distinct configurations neither is an estimate of anything, and both
report `INCONCLUSIVE` rather than a confident number resting on two samples.

Pre-registration is checked, not assumed. The claim is frozen before the
backtest and re-derived at judge time; if the hypothesis or the parameters moved
in between, G1 fails.

## Local MCP tools

AlgoForge exposes the same bounded action registry used by the interface and
orchestrator as a local stdio MCP server. Read-only is the default:

```powershell
uv run algoforge-mcp
```

To expose research-writing and engine-control actions, the client configuration
must opt in explicitly:

```powershell
uv run algoforge-mcp --write
```

Write mode still has no arbitrary-code or live-order tool. Every call uses the
existing action validation and activity log. Because this is stdio MCP, stdout
is reserved for the protocol; diagnostics belong on stderr.

## Verify

```powershell
.\scripts\Test-AlgoForge.ps1
```

The workstation includes immutable run contracts, point-in-time data validation, deterministic evidence gates, fixed-seed risk paths, separately versioned challenge and funded account rules, cited agent dissent, and a human-gated ForgeKeeper release supervisor. Architecture and the executable plan are in `docs/superpowers/`.
