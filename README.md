# AlgoForge

AlgoForge is a private, local, paper-only desktop application for building, running and judging trading strategies. It writes strategies to disk as real Python, backtests them on real market data, judges them through a deterministic gate ladder, and simulates how they would fare under funded-account rules.

It opens on four operating environments rather than one workspace. Every one of them reaches the same data, the same strategies and the same judge; what changes is what the screen is arranged around, and how much an assistant may do on your behalf.

| Mode | For | What it is arranged around |
| --- | --- | --- |
| **Normal** | Conventional trading and research | Charts, the book, strategies and their evidence |
| **Prop Firm** | A funded or evaluation account | One question: how close am I to breaching |
| **AI** | Research and workflow automation | The bounded action registry, and what an assistant may call |
| **Hedge Fund** | Institutional-style quantitative work | The loop: data, research, alpha, validation, portfolio, risk, gate, execution, operations, performance |

Modes are not tiers and nothing is locked: every mode can reach validation, and switching modes never deletes work. Each remembers its own layout, so leaving one and returning is a return rather than a reset.

**Market data is real.** CME futures come from Databento (cost-checked before each download; roughly $0.025 per week of MNQ 1-minute bars) and crypto from Binance public endpoints (free). A seeded, deliberately edge-free synthetic dataset is available for offline work and can never clear the judge's G0 data gate.

**Fills are modelled, not calibrated.** Backtests apply commission and ATR-proportional slippage, but they have not been reconciled against a live platform. Calibrate against NinjaTrader's Strategy Analyzer before trusting any number. No live-order, account-signup, payment, KYC, or production-broker capability exists.

## The four modes

**Normal** is the least opinionated: it supplies the tools and leaves the
arrangement to you.

**Prop Firm** evaluates a rule set *you* supply against the account's recorded
state — starting balance, daily loss limit, static or trailing drawdown, profit
target, contract and position caps, session windows, consistency, and any
firm-specific limit expressed over a metric the account state actually measures.
AlgoForge asserts nothing about what any named firm's live contract says, ships
no firm's numbers as a default, and refuses to invent a balance: an account
nobody has recorded reads "nothing has been recorded" rather than showing a
comfortable buffer against a starting balance that may not be current. A rule
that cannot be checked reports `NOT MEASURED`, on the same three-valued
discipline the judge uses.

**AI** reaches the same bounded action registry the interface uses. There is no
verb here the interface does not also have, and no arbitrary-code verb at all.
The Actions screen shows the policy per action, computed by the same function
that enforces it.

**Hedge Fund** is the whole quantitative loop, and it asks one more question on
the way in: *human in the loop*, or *autonomous*. On either stance the
deterministic controls are the same and an assistant cannot change them.

## What an assistant may do, and where that is decided

`packages/forge/modes/permissions.py` is a pure function of four facts — who is
asking, which mode, which stance, and what the action is. Nothing a model
generates can set any of them.

* **Preparation** — research, backtests, validation, portfolio construction,
  order preparation, screening — is permitted in every mode. An assistant that
  must ask before running a backtest is not assisting.
* **Automation** — starting the engine, dispatching a specialist — belongs to
  the AI and Hedge Fund modes.
* **Reaching the book** is permitted on exactly one configuration: Hedge Fund on
  the autonomous stance, and even there the order still passes the pre-trade
  gate.
* **Protected controls** — risk limits, prop rules, the fund configuration, the
  kill switch, the operating mode and the stance itself — are denied to an
  assistant in every mode and on every stance. The mode and stance actions are
  protected precisely so an agent cannot widen its own permissions by switching
  to the stance that would grant them.

Anything else that writes is held in an approval queue with the arguments it
would have used. Approving runs it *as you, now*, so it re-validates against
current state. Every call — allowed, held, denied or failed — lands in an
append-only audit log with the ruling and the reason, so an unattended run is
answerable rather than merely logged.

## The Prop Desk

Prop Firm mode opens on one account. The **Prop Desk** is the same discipline
across many of them: connected accounts, what each is permitted to do, which
validated strategy each should be running, and every order the desk refused with
the reason.

**No live broker connector exists.** Rithmic, Tradovate and ProjectX are
*declared* — each carries its real authentication method, account key fields,
bracket model, published rate limit, session lifetime and documented
limitations — and each refuses every command with the reason and with what a
live connector would still need, separated into engineering work and external
requirements nobody can engineer around (Rithmic's conformance process, a
Tradovate OAuth application, a ProjectX tenant subscription). The only adapter
that executes is a local simulator, and it labels every fill simulated.

**Providers are not platforms.** A provider is execution and account
infrastructure that holds the account records. A platform is a front end. That
distinction is modelled rather than assumed: NinjaTrader Desktop is a platform
whose accounts belong to whichever provider it is connected to, NinjaTrader
*Brokerage* accounts are Tradovate accounts, TopstepX is a ProjectX tenant, and
a data feed's `routes_orders` is a `Literal[False]` rather than a setting.

**Every order climbs one ladder**, and there is no other path from an intent to
an adapter:

```
account bound → connection live → what backs this order → firm permission
  → cross-account direction → news blackout → the account's own rule engine
  → the pre-trade gate → the execution fabric → the provider
```

Rungs six and seven are the *existing* engines — `forge.prop.account.assess` and
`forge.execution.gate.screen` — called, not reimplemented. A refused order keeps
the whole ladder on the record, so "why is this follower flat while the leader is
long three" is answered per stage, in words.

**Unknown is never permission.** A firm's rules are four-valued — `ALLOWED`,
`BLOCKED`, `UNKNOWN`, `REQUIRES_CONFIRMATION` — and AlgoForge ships none of them.
Every permission starts `UNKNOWN`, which blocks every automatic action, because
the research found no blanket rule across seven firms: copying allowed at some
and only with approved tools at others, bots banned on one firm's tier and
permitted at another, and one firm requiring that orders originate from the
trader's own device.

**The copy engine converges on net position targets.** A follower's target is a
function of the leader's *current* net position, not of the stream of events that
produced it — so a duplicated event, a reordered one or a missed one all converge
to the same place, and a rounding rule that over-sizes on each of four partial
fills cannot accumulate. Divergence is resolved against the provider, which is
authoritative: a snapshot replaces local state rather than merging with it.

**Allocation is deterministic; advice can only narrow it.** The feasible set is
computed from the judge's verdict, out-of-sample strategy health, firm
compatibility, the account's rule engine and a risk budget against its own buffer
to the loss floor. A recommendation — from a model or from anywhere else — can
reorder inside that set and reduce a size. It cannot introduce a pairing and it
cannot raise a size, and `tests/propdesk/test_desk_allocation.py` asserts both
against deliberately hostile input.

**News can add a restriction and never removes one.** Forex Factory publishes no
official calendar API and returns HTTP 403 to automated requests, so AlgoForge
does not scrape it and ships no scraped dataset. What ships is a provider
interface with two implementations: an operator-recorded calendar that works
offline, and a real client for the Federal Reserve Bank of St. Louis FRED
release-dates API, which refuses without a free `FRED_API_KEY` and carries the
attribution its terms require. FRED gives release *dates* and not clock times,
and every event it returns says so rather than supporting a fifteen-minute window
in the wrong place.

**No VPS anywhere.** The execution fabric runs wherever the application runs. An
always-on agent is a deployment option nobody has to take, and no hosting
provider is named in the code.

## The fund loop

Data, research, alpha, validation, portfolio construction, risk, the pre-trade
gate, execution, operations, performance, feedback — and back to research. Each
stage reports a state read from a real record, and a stage nobody could measure
says so rather than drawing a plausible number.

Three properties hold across it:

* **The judge stays authoritative.** Portfolio construction refuses to size a
  signal whose strategy the judge failed, and the gate refuses an order from
  one, both reading the verdict through the same path the Evidence screen uses.
* **Nothing routes around the gate.** A cleared order leaves the gate with a
  clearance bound to a hash of that exact order. The OMS recomputes the hash and
  refuses anything that does not match, so an order edited after screening is
  refused. There is no flag, argument or privileged caller that skips it.
* **Every fill is simulated, on the record.** The only adapter is a local
  simulator; `simulated`, the venue and the pricing basis are fields on the fill
  rather than a caption, so they cannot be lost by a component that forgets to
  draw them. An open position's unrealised P&L is reported absent rather than
  zero — this build has no mark-to-market feed, and a zero would read as flat.

No Bloomberg, EMSX, Charles River, Axioma or Barra is required, or faked. Where
one would sit — a commercial factor model, a broker — there is a typed seam and
a stated limitation. Covariance is estimated locally with Ledoit-Wolf shrinkage
and refuses to produce an estimate at all when the sample is too thin, rather
than handing the optimiser a singular matrix whose null space looks like a very
good portfolio.

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

## Research campaigns

Starting the engine on its own is a parameter search: it draws from each
template's declared ranges and tests the numbers. That is a real thing to do and
it is still available, but it is not research, because the set of *questions* is
fixed at whatever the catalogue happens to hold.

A **campaign** gives it a question. It names an objective, fixes the dataset and
the universe, declares the budgets, and carries an explicit allocation across
five kinds of search:

| | |
|---|---|
| New hypotheses and mechanisms | 35% |
| New families and their templates | 25% |
| Advancing research already going somewhere | 20% |
| Parameter refinement | 10% |
| Robustness and replication | 10% |

The split is configurable and adapts, within a bounded drift, from what the
frontier actually contains — a backlog of promising work pulls budget towards
advancing it; family proposals that keep colliding with existing families pull
budget away from discovery. The realised split is recorded next to the intended
one, because an allocation nobody checks is a comment.

Each cycle the campaign draws a bucket and composes a candidate for it. For the
discovery buckets that means proposing a family and composing a template — as a
`StrategyDefinition`, which is **data**, put through the same IR validation,
static guard, IR-versus-Python ledger check and synthetic smoke test an
operator-written template faces. Nothing generates Python; the exporter renders
it and the guard refuses it if it reaches for the filesystem, the network, a
subprocess or dynamic execution.

Everything after that line is the pipeline that was already there: the memory
gates, the frozen pre-registration, conformance, determinism, the chronological
split, the validation grid, the judge, the burn-once holdout. G0-G13 are
unchanged, and nothing in the research layer can reach the judge's input.

### The frontier

Every question the system holds sits in one of nine states, and three of them
exist to stop a gap being mistaken for a verdict:

- `UNTESTED` — admitted, never run. **Not a failure.**
- `INCONCLUSIVE` — run, and the evidence could not say. Also not a failure.
- `BLOCKED_BY_DATA` — needs data this installation cannot serve. Says nothing
  about whether the claim is true.

The frontier has no veto. Only research memory, acting on a classified failure
with a declared reach, may decline to spend compute — a map that could delete
territory would stop being a map.

### Failures become questions

A failed experiment is the middle of a piece of research, not the end of one.
When a result concentrates in a condition, the engine derives the questions that
follow — *is the edge conditional on volatility expansion rather than volatility
level?* — and admits them as `UNTESTED` hypotheses with an edge back to what
suggested them. Every generated question passes the same falsifiability bar a
human-written one does, and the same novelty check. Bookkeeping, data, lookahead
and safety failures deliberately generate nothing: a full disk says nothing
about the market, and the right follow-up to a lookahead defect is a fix.

### Novelty

Before a family or a template is created, the proposal is compared against every
existing family, template and hypothesis — on the claim, the mechanism, and the
structure of the signal. The comparison is deterministic and offline, so it
gives the same answer twice and can be tested. A restatement is refused with the
collision named, which is why there is no `mean_reversion_2`.

### External research

With web research enabled, the campaign searches arXiv and Crossref for
mechanisms and stores what it finds with the URL, the source, the publication
date where the index supplied one, the retrieval timestamp, and claims copied
**verbatim** out of the returned abstract. There is no path that constructs a
citation and no fallback that invents one: with no network it stores nothing and
records that it looked.

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

## Workspaces

A workspace is a screen layout you own — panels on a twelve-column grid, arranged
however you want them. It is not a mode and does not gate anything.

- **It survives a restart.** Reopening the application restores the last
  workspace that was open, falling back to the one you marked as default.
  Opening a workspace is not the same as making it your default, and the two are
  separate controls.
- **It has a history.** Every meaningful save keeps a version with a change
  summary, who made it, and the version it came from — so an edit the agent made
  is visible as one, and reversible. Restoring is recorded as a new version on
  top rather than a rewind, so the work in between stays readable.
- **It travels.** Export writes a portable document carrying the layout and
  nothing else: no research, no credentials, no identifiers to collide on the
  way in. Import validates every panel through the same models the rest of the
  application uses.
- **Deleting one deletes no research.** A workspace holds no experiment, verdict
  or holdout; the two live in different places on purpose, and either can be
  deleted without touching the other.

Every one of these is a registered action before it is a route, so "duplicate
this desk for ES" typed at the assistant and clicked in the manager are the same
call. There is no AI-only path into a workspace.

## Experiments

The **Experiments** tab is the search as a record rather than a feed: what was
tried, what came of it, and the line each candidate came from. Failure is
first-class — most candidates are rejected, and `INCONCLUSIVE` is counted
separately because nothing was disproven, the evidence was simply never
produced.

Every judged run is also snapshotted. The snapshot holds the verdict, the data
identity, the frozen hypothesis, and content-addressed copies of the judge and
statistics source that produced it — so a result can be re-examined later rather
than only detected as having changed. `verify` asks whether the record is
undamaged; `drift` asks whether the rules have moved since. A verdict from a
judge that has been edited is still an honest record of what that judge decided.

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
