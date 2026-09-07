# AlgoForge Qanat-style control plane

Authorized scope: continue the locally modified AlgoForge checkout; repair Agent
Command; make research and family discovery continuous while the application is
running; expose validation and prop-path simulation visibly; adopt the useful
interaction patterns from Qanat; add an MCP surface over the existing action
registry; verify, document, commit, and push. Preserve the deterministic judge,
evidence tiers, burn-once holdout, paper-only boundary, and existing engine.

## Options considered

1. **Replace AlgoForge with Qanat.** Rejected. Qanat is a small public-beta DAG
   engine for table pipelines. AlgoForge already has futures contract units,
   chronological partitions, CSCV/PBO, walk-forward selection, CPCV paths,
   holdout lineage, prop-rule replay, and a deterministic judge.
2. **Run two equal engines.** Rejected as the default. Equal writers create two
   ledgers, two lifecycle authorities, and ambiguous verdicts. A second engine is
   useful only as a read-only shadow oracle whose output is compared and labelled.
3. **One engine plus a shared control plane.** Selected. React, REST, the internal
   orchestrator, the continuous research loop, and MCP all call the same typed
   action registry. AlgoForge remains the sole writer of evidence.

## Reliability and agent topology

`AgentService` exposes the orchestrator separately from directly assignable
specialists. Agent Command renders the orchestrator as the centre and computes
specialist positions from the number returned by the API; adding a role can no
longer crash the page. A route-level error boundary turns any future rendering
failure into a recoverable diagnostic instead of a blank screen. Browser tests
mount the real nine-role API contract and assert zero page errors.

## Continuous research loop

A lifecycle-owned `ResearchLoop` runs independently of the strategy workers for
as long as the local API is open. It has an enabled switch, cadence, bounded topic
agenda, daily call ceiling, pause/backoff state, durable receipts, and a one-run
trigger. Each cycle searches primary scholarly metadata, deduplicates sources,
then asks the hypothesis and strategy specialists to process genuinely new
material. The orchestrator may register a new family without prompting only when
it records an economic mechanism and data requirements. A family whose required
data is unavailable is still created as `BLOCKED_DATA`; it cannot receive a
runnable strategy. No fetched paper or model response executes as code.

“Always researching” means while the AlgoForge application is running. It does
not mean while the PC is off, and it does not bypass provider budgets, network
backoff, evidence gates, or the operator pause control.

## Validation and simulation visibility

A Validation Lab section makes existing computation inspectable. It shows:

- walk-forward fold geometry, in-sample versus out-of-sample Sharpe, efficiency,
  consistency, purge and embargo;
- CSCV probability of backtest overfitting and CPCV reconstructed path spread;
- prop Monte Carlo configuration, block-bootstrap method, path count, target/loss
  race, confidence interval, terminal distribution, drawdown map, and equity paths;
- explicit `NOT_TESTED`, `INSUFFICIENT_DAYS`, stale-code, stale-data, and
  unverified-rule states.

The UI never calls CPCV “Monte Carlo.” Prop simulation is labelled as a stationary
five-day block bootstrap of observed daily P&L, not a funded-account forecast.

## Qanat-derived interface direction

Adopt Qanat’s warm instrument palette and graph grammar without copying its
product identity: near-black `#0a0a0a`, warm panels `#161513`/`#1d1b19`, paper
text `#f4f2ed`, lime action key `#a2e65d`, gold uncertainty `#e8c069`, and brick
failure `#c1503f`. Keep AlgoForge evidence-tier colours distinct.

The signature surface is a left-to-right evidence graph. Nodes open their own
contents and controls. Packets cross edges only when the corresponding job has
actually emitted an event; idle animation is removed. Heatmaps cover family by
dataset evidence, strategy by validation gate, and strategy by prop rule. Motion
has one-to-one operational meaning and disappears under reduced-motion.

## MCP and secondary engines

Add a local stdio MCP server using the official Python MCP SDK. Read-only mode is
the default and omits mutating actions; an explicit `--write` switch exposes the
same guarded mutations available in the app. Tool results include structured
content and action receipts. The MCP adapter contains no business logic.

Future second engines attach through the existing oracle boundary. They receive a
frozen strategy/data receipt and return a comparison artifact. They never write
AlgoForge verdicts, consume holdout, or promote a strategy.

## Verification

Required gates: targeted failing tests first; full pytest, ruff, strict mypy,
Vitest, production build; real browser click-through at 1280 and 1920 pixels;
reduced-motion and no-page-error assertions; isolated continuous-loop run;
MCP initialize/list/call smoke in read-only and write-enabled modes; vault notes,
STATUS, session log, README and Git evidence updated. Pushing is allowed only
after the checkout is cleanly committed and all required gates pass.
