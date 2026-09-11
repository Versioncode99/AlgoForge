# Prop Desk: implementation report

Branch `claude/wonderful-goldberg-twi9xv`, six commits, 85 files changed.
Not merged to `main`.

---

## 1. What this build can and cannot do

**It cannot trade.** There is no live broker connector anywhere in it. The
statement is enforced in three independent places, not asserted in prose:

- `forge.execution.lifecycle.LIVE_EXECUTION_AVAILABLE` is `False`, and
  `check_transition` refuses the `DEPLOYED` stage with a reason naming the
  missing connector. This module was not modified by any of this work.
- Rithmic, Tradovate and ProjectX ship as `DeclaredAdapter`s that raise
  `AdapterUnavailable` on every method. The constructor refuses a provider
  descriptor claiming a live connector, so one cannot be mistaken for the other.
- `config/capabilities.json` records `broker_connector_live: false`.

`tests/propdesk/test_funnel.py::test_the_strongest_possible_case_is_still_blocked_by_the_missing_connector`
builds the most favourable case the system permits — a PASS verdict,
out-of-sample evidence, surviving walk-forward and CPCV, a healthy grade, an
ALLOWED firm policy, a live simulated connection, an acknowledged disclosure and
`FULLY_AUTONOMOUS` — and asserts it is still blocked, by the lifecycle gate.

**What it does do:** hold many accounts behind one set of deterministic
controls, mirror one account's net position onto others through a real order
state machine, decide which validated strategy each account should run, size
that decision from a bootstrapped drawdown distribution, and record every
refusal with the rung that produced it. Execution reaches AlgoForge's own
simulator and every fill is labelled simulated.

---

## 2. Architecture

```
  research ─► validation ─► judge (G0–G13) ─► strategy library
                                                    │
                                                    ▼
                              account ─────► allocation ◄──── risk mode
                            (prop rules)         │           (one fraction)
                                                 ▼
                                          the desk funnel
                                          8 recorded rungs
                                                 │
                                                 ▼
                                        pre-trade gate ─► execution fabric
                                                                │
                                                                ▼
                                                        provider adapter
                                                                │
                                                                ▼
                                                    reconciliation ─► monitoring
                                                                          │
                                                                          ▼
                                                                   reallocation
```

The load-bearing property is that **the advisory layer touches exactly one
number.** `AllocationConstraints.risk_fraction` — the share of an account's
buffer to its loss floor that one allocation may cost. Everything from there to
an order is deterministic: contracts, feasibility, the eight rungs, the gate.
An adjuster that can only move one bounded scalar, whose range the operator
fixes, and whose conversion to an order is deterministic and gated, cannot reach
anything by being wrong about the number.

---

## 3. Files changed

**New packages**

| path | modules | what |
| --- | --- | --- |
| `packages/forge/propdesk/` | 18 | identity, credentials, instruments, orders, fabric, reconcile, copy, policy, allocation, news, desk, store, risk, survival, scaling, autonomy, consent, audit, plus `adapters/` |
| `packages/forge/explain/` | 4 | metrics, why, preview, passport |
| `packages/forge/modes/` | +2 | expertise, intents |

14,436 lines across those.

**API** — `apps/api/forge_api/propdesk.py` (new, the service and 32 routes),
`actions.py` (+34 registered desk actions), `control.py` (wiring, the evidence
supplier, 6 explanation routes, the passport assembler), `dossier.py`
(`latest_verdict` extracted), `main.py`.

**Frontend** — `propdesk.ts`, `explain.ts`, `views/PropDesk.tsx`,
`views/ModeSelect.tsx`, `components/PanelBody.tsx`, `App.tsx`,
`styles/propdesk.css`, `styles/modes.css`, and four test files.

**Modified elsewhere** — `forge/workstation/models.py` and `templates.py`,
`forge/modes/models.py`, `config/capabilities.json`, `README.md`,
`tests/execution/test_boundary.py`, `tests/api/test_mcp_server.py`.

---

## 4. The account and provider model

The dossier's central finding is that copy vendors integrate with *execution
infrastructure*, not with trading platforms, and that conflating the two is the
source of most of their support load. `forge.propdesk.identity` makes them
different types:

- **Provider** — routes orders and holds accounts. SIMULATED, RITHMIC,
  TRADOVATE, PROJECTX. A closed set.
- **Platform** — a front end. NinjaTrader Desktop is a platform and has *no*
  provider; NinjaTrader **Brokerage** is Tradovate; TopstepX is a ProjectX
  tenant.
- **ExecutionVenue**, **DataFeed** — separate again; only an exchange may set
  `matches_public_book`, and a feed's `routes_orders` is `Literal[False]`.
- **AccountKey** — `(provider, environment, credential_ref, account_id,
  qualifiers)`. Display name is deliberately excluded: one vendor shipped a fix
  for renames deleting copy configurations.

Adapters expose `connect / disconnect / authenticate / discover_accounts /
discover_capabilities / place / modify / cancel / flatten / poll / snapshot /
health`.

---

## 5. Execution fabric

`forge.propdesk.fabric` and `orders`:

- A priority command queue — flatten before cancel before protective before
  entry — so a de-risking instruction never queues behind an entry.
- A rate budget with continuous refill and penalty support.
- An outbox, and one attempt per flush: a transport fault requeues rather than
  retrying inside the drain, which previously let one flush consume several
  attempts.
- An order state machine with legal transitions and a rank guard. `MODIFIED` is
  special-cased so it never moves the lifecycle rank — without that, a stop
  moved after a partial fill was silently dropped as "stale".
- Deterministic idempotency keys, `hash(group, source_order, marker, account,
  action)`, capped at 50 characters to fit provider tag fields.

`reconcile`: broker snapshots **replace** local state, never merge, with a
staleness guard so an event older than the latest snapshot cannot drive a
correction.

---

## 6. Copy engine

Followers converge on the leader's **current net position**, not on event
deltas. That single choice is what makes duplicate, reordered and dropped events
survivable: a follower computes what it should hold and closes the difference,
so no error accumulates. Sizing is fixed, multiplier, proportional or
risk-budget; instrument mapping across contract sizes reports its own exposure
error rather than hiding it.

Every copied order climbs the same eight rungs as any other.

---

## 7. Strategy allocation

`forge.propdesk.allocation` answers "which validated strategy should this
account be running right now" from: the judge's verdict, out-of-sample Sharpe
and trade count, live-versus-baseline expectancy drift, bootstrapped p95
drawdown, regime coverage, the account's buffer and contract caps, the firm
policy, and a churn limit read from the append-only history.

Health grades: `HEALTHY / WATCH / DEGRADED / UNPROVEN / FAILING`. Only HEALTHY
allocates automatically; WATCH and DEGRADED are offered for a person to confirm
and are refused without one. Advice may reorder within the feasible set and
reduce sizes inside it — never add a pairing, never raise a size.

---

## 8. Risk architecture

### Three modes

| | promise | who moves the number |
| --- | --- | --- |
| **MANUAL** | I decide. | nobody |
| **ADAPTIVE** | AlgoForge calculates. | a deterministic function of measured state |
| **AI RISK MANAGEMENT** | AlgoForge manages within my boundaries. | the same function, then an advisory factor that may only reduce |

### The appetite meter is not a size dial

A meter that maps 52 to "two contracts" is a slider with extra steps. This one
changes *what question is asked*, in two statistical ways:

1. **How far into the tail you size against** — conservative uses the
   99th-percentile bootstrapped drawdown, aggressive the 75th. On a thin-tailed
   strategy those are nearly the same number; on a fat-tailed one they differ by
   a factor of several. The meter's effect is therefore a property of the
   strategy's measured behaviour.
2. **What share of the buffer is at stake**, between the operator's own minimum
   and maximum.

The distribution comes from a stationary block bootstrap over out-of-sample
per-contract daily PnL — contiguous five-day runs, so a strategy that loses in
streaks keeps its streaks. With no series, or one shorter than 30 days, there is
no band: `Unmeasurable`, with the shortfall named, and the fraction falls to the
operator's minimum. No normality assumption, no default, no substitute.

### Controlled scaling

Eight drivers multiply the band's target, each in `[0, 1]`: buffer, strategy
health, realised volatility, regime fit, correlation, account rules, news,
evidence. Three rules make it safe:

- **A driver can only cut.** Risk rises only when the band itself rises.
- **An increase requires every driver measured; a decrease requires none.**
- **De-risking is never rate-limited; increases always are.**

Balance is not a driver. It enters through the buffer — a constraint — and
through the band, where a larger buffer pays for more contracts at the *same*
fraction. Equity growth with a degraded strategy moves nothing. That is §11's
headline refusal, and `test_balance_alone_is_not_a_driver` holds it.

The governor applies, in order: boundaries, hysteresis, then the asymmetric part
— a decrease passes; an increase must survive the unmeasured check, cooldown,
step and the daily cap. An emergency decrease (buffer at the operator's
threshold, or a breached rule) skips cooldown and step entirely.

### "Why did AlgoForge change my risk?"

Not generated. `RiskProposal` carries the drivers with their observed values and
effects, the one that bound, the governor clamp, and the contract counts before
and after. The panel's sentences are read off those fields. There is no function
that produces prose without a proposal to produce it from.

---

## 9. AI permissions

Six capabilities the operator may grant, each of which narrows something. Eight
prohibitions, each paired with the module that enforces it:

| may never | enforced by |
| --- | --- |
| Override prop-firm limits | `forge.prop.account.assess` |
| Override your maximum risk | `forge.propdesk.risk.RiskBoundaries` |
| Bypass the pre-trade gate | `forge.execution.gate.screen` |
| Deploy an unvalidated strategy | `forge.propdesk.autonomy.MANDATORY_GATES` |
| Trade a blocked account | `forge.propdesk.desk.PropDesk` |
| Bypass G0–G13 | `forge.judge.engine` |
| Change a protected control | `forge.modes.permissions` |
| Invent a permission a firm has not given | `forge.propdesk.policy.Permission` |

Two tests import every `enforced_by` and assert the module and attribute exist,
so the list cannot become aspirational. A third takes each risk-writing action
through `forge.modes.permissions.evaluate` for every mode and stance and asserts
`DENY`.

---

## 10. Autonomous deployment

`OFF / APPROVAL_REQUIRED / FULLY_AUTONOMOUS` over **one** thirteen-gate list
that every level evaluates identically — `MANDATORY_GATES` is a single
module-level tuple and the test asserts the evaluated kinds match at all three
levels. The level changes only what happens once every gate has passed: record,
submit to the existing `forge.hedgefund.approvals` queue, or proceed.

Any gate that is `unknown` blocks. `DeploymentFacts` has every field optional,
so a source the service could not reach produces a *blocked* deployment rather
than an unvalidated one.

---

## 11. Warnings and consent

Four versioned disclosures: AI risk management, autonomous deployment, copy
trading, simulated execution. The version is a content hash of the text, so
editing a sentence invalidates every acknowledgement of the old one — there is
no way to change what somebody agreed to while keeping their agreement.

Every factual claim names the control that makes it true, and a test imports
each one. A validator refuses indemnity language at construction
(`indemnify`, `hold harmless`, `waives any`, `not liable`, `assumes all risk`,
…), so the file cannot become a liability document.

On screen: the dialog appears where the switch is, each statement has its own
checkbox, and confirm stays disabled until all are ticked.

---

## 12. Audit trail

`ConsequentialRecord` carries every field §17 asks for: actor, account, action,
previous and new state, risk configuration, strategy, validation state, policy
state, automation mode, reason, timestamp, disclosure version acknowledged,
approval status, system decision, resulting execution state.

Written to an append-only table with no update and no delete. A validator
scrubs secret-shaped keys at any depth on the way in, and two tests plant five
such keys, write the record, and read the SQLite file back **as bytes** — the
only check a post-hoc redaction cannot satisfy.

---

## 13. Prop-firm policy engine

Every capability resolves to `ALLOWED / BLOCKED / UNKNOWN /
REQUIRES_CONFIRMATION`. All permissions default to UNKNOWN; hedging and
third-party copy default to BLOCKED. UNKNOWN never satisfies a check requiring
ALLOWED, and no code path converts one into the other. A cross-account direction
guard works on product *groups*, so NQ and MNQ are one exposure across all of an
owner's accounts.

AlgoForge ships no firm's rules and asserts nothing about what any named firm's
contract says.

---

## 14. Economic calendar

FRED release dates via the official St. Louis Fed API, with the required
attribution notice, `include_release_dates_with_no_data=true`, and
`time_is_approximate=True` on every event — FRED gives dates, not times.

**Forex Factory is recorded as unsuitable.** There is no official API;
`https://www.forexfactory.com/tos` returned HTTP 403 to an automated request
during this work; every "Forex Factory API" is a third-party scraper. That
finding is in the code (`SUGGESTED_SOURCES`), on the News screen, and in the
README. Nothing scrapes it.

A firm whose news restrictions are unrecorded reads UNKNOWN.

---

## 15. Security

- `Secret` redacts in `repr` and `str`, refuses to pickle (`__reduce__` raises),
  and fingerprints without revealing.
- `CredentialRecord` stores a provider, a label, non-secret public fields and
  the *name of an environment variable*; a validator refuses a public field
  named like a secret.
- `CredentialBroker` records every access without the value.
- Audited during this work: eleven desk endpoints scanned for secret-shaped
  keys, the running SQLite file across all nineteen tables, both server logs,
  and the branch's git history for `sk-…`, `AKIA…` and PEM private-key headers.
  Zero matches anywhere.

No API key was fabricated, no access control bypassed, no prohibited source
scraped, and no third party's credentials embedded.

---

## 16. Tests

| suite | count |
| --- | --- |
| Full Python suite | **2330 passed** |
| New tests in this work | **718** |
| Frontend (`vitest`) | **86 passed** |

`ruff` clean, `mypy --strict` clean across 177 source files, `tsc --noEmit`
clean.

Coverage by §34's list: order and position state machines, partial fills,
rejections, duplicate events, disconnect, reconnect, reconciliation; leader and
follower, sizing, mapping, duplicate prevention, failure handling; prop limits,
blocked actions, unknown rules, confirmation states; manual, adaptive and
AI-managed risk, scaling, de-risking, volatility and drawdown changes, buffer
protection, cooldown and hysteresis, hard limits; AI cannot bypass a
deterministic control, exceed a ceiling, deploy a blocked strategy, trade a
blocked account or bypass G0–G13; approval-required and autonomous deployment
with the validation, policy and pre-trade gates; and the UX set — state
transitions, warnings, acknowledgement persistence, audit records.

`tests/propdesk/test_funnel.py` is the end-to-end one: twelve tests that follow
a judged strategy through risk, allocation and execution, then break things and
watch the same machinery refuse.

---

## 17. Visual QA

The application was built and run — API and Vite, against a seeded workspace
with two simulated accounts, a 50k rule set, a recorded firm policy and a
declared Rithmic connection — and driven in Chromium. Every Prop Desk section
was opened, screenshotted full-page, and its rendered text read back. The
disclosure flow was driven end to end and the acknowledgement, its version and
the resulting audit rows were read back out of the live API.

**Running it found six bugs the fixture-backed tests did not.**

1. The Risk screen crashed on a stored proposal: the row holds the canonical
   model and the screen reads derived keys. Rendered on the way out now.
2. The rules driver knew three level names; the rule engine has five, so
   *caution* and *warning* accounts read as unmeasured rather than as reasons to
   cut. Keyed to the enum itself now.
3. A sparse rule set locked risk at the minimum forever, because seven
   unconfigured optional rules made the account's worst level permanently
   `NOT_ASSESSED`. The service reads the worst level among *configured* rules
   now, and still treats a configured-but-uncheckable rule as unknown.
4. The front door navigated nowhere — React Query drops `mutate` callbacks when
   the calling component unmounts, and the shell then rewrote the hash against
   the outgoing mode's manifest.
5. An unjudged strategy's passport had no "what did not hold" section at all.
6. Two heading and layout slips.

---

## 18. Simulated versus real

| capability | state |
| --- | --- |
| Multi-account desk, copy engine, allocation, risk modes, autonomy gates, consent, audit | **real** |
| Execution | **simulated** — a local book with partial fills, resting limits, cancel/fill races and contract caps |
| Rithmic / Tradovate / ProjectX | **declared** — real interfaces, every command refused, required work stated |
| Economic calendar | **real** for FRED release dates; manual entry otherwise |
| Live deployment | **refused** by `forge.execution.lifecycle` |

---

## 19. Known limitations

- No live broker connector. Simulator fills are modelled, not calibrated against
  any venue: no queue position, no depth, no adverse selection.
- Five CME symbols (MES, YM, MYM, RTY, M2K) carry `verified=False` because their
  specifications could not be confirmed from a primary source during this work.
- FRED supplies release *dates*; event times are flagged approximate.
- Correlation is measured across allocated strategies' out-of-sample series. With
  fewer than two such series it is unmeasured, which blocks an increase.
- `DeskContext.capital` defaults to 0 because `PortfolioLimits.max_position_weight`
  is capped at 1.0, which no single leveraged futures contract can satisfy.
  Account contract caps and the loss floor bind instead; supplying capital
  engages the stricter notional checks, and a test proves it.
- Every prop programme's permissions start UNKNOWN and must be recorded by the
  operator.

---

## 20. Production work remaining for live execution

1. Implement one real adapter end to end: protocol, session lifecycle, symbol
   discovery, event normalisation, reconnect.
2. Obtain credentials; for Rithmic, pass conformance certification before
   connecting to production systems.
3. Run reconciliation against a real demo account for an extended period and
   measure divergence rates.
4. Calibrate the rate budget and latency assumptions against observed limits.
5. Record each prop programme's actual permissions, since all are UNKNOWN today.
6. Verify the five unverified contract specifications from exchange sources.
7. Decide, deliberately, to flip `LIVE_EXECUTION_AVAILABLE` — and accept that
   every gate downstream of it becomes load-bearing on that day.

---

## 21. What was deliberately not done

- No deterministic control was weakened. `forge/judge/`, `forge/execution/gate.py`,
  `forge/execution/lifecycle.py`, `forge/risk/` and `forge/prop/account.py` are
  unmodified.
- No broker API, prop-firm permission, data source, citation or integration was
  invented.
- No VPS is an architectural dependency. `AlgoForge → fabric → adapter → venue`,
  not `AlgoForge → VPS → platform → broker`.
- No disclosure makes a legal claim.
- No statistic was manufactured, no backtest fabricated, no inconclusive result
  converted into a success.
