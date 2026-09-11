# Prop Desk — architecture and implementation plan

*Written 2026-09-10, after a repository audit and a read of the TradeSyncer /
Tradecopia forensic dossier (34 files). Implemented in `packages/forge/propdesk`.*

---

## 1. What the dossier actually says, and what follows from it

Five findings shaped every decision below.

1. **Copiers integrate with execution *infrastructure*, not with trading
   platforms.** TradeSyncer's "NinjaTrader" entry is Tradovate OAuth and its
   "Tradesea" entry is the Rithmic form. So `Provider` is a closed set of
   infrastructures, `Platform` is a separate, non-executing concept, and a
   platform that is an alias declares the provider it binds to.
2. **Account identity must be provider-qualified.** One credential fans out to
   many accounts, and rate limits and sessions attach to the *credential*.
   `AccountKey` is `(provider, environment, credential_ref, account_id)` and the
   display name is explicitly not part of it — Tradecopia had to ship a fix for
   "account renames silently delete copy-trade configurations".
3. **Correctness comes from reconciliation, not replication.** Both products
   converge to broker snapshots. So the broker/provider is the source of truth,
   local state is a cache plus an intent log, and follower targets are computed
   from *net positions* rather than from event deltas.
4. **No blanket prop-firm rule exists.** Copying is allowed at some firms and
   only with approved copiers at others; TPT PRO bans bots, ETF bans AI
   decision systems, Topstep requires orders to originate from a personal
   device. Therefore: unknown is never permission, and nothing hard-codes a
   named firm's contract.
5. **A VPS is not required.** Broker-API copiers connect from wherever they run.
   Persistent always-on execution is a *deployment option*, not a dependency.

## 2. Pipeline

```
Research Factory → Strategy Library → Strategy Health → Prop Account Compatibility
   → Strategy Allocation → Risk / Prop Rules Gate → Execution Fabric
   → Broker/API Adapter → Account
```

The copy engine is an execution primitive *inside* this pipeline: a leader
account is one signal source among several, and `Leader → Copy Group →
Followers` is the degenerate case of `Strategy → Allocation → Accounts`
(dossier file 27, §3).

## 3. What is reused rather than rebuilt

| Existing | Used for |
|---|---|
| `forge.prop.account` (`AccountRules`, `assess`, `Level`) | every numeric account rule; the Prop Desk adds *permissions* and compatibility, not a second rule engine |
| `forge.prop.accounts.PropAccountStore` | configured accounts and recorded state |
| `forge.execution.gate.screen` | the pre-trade gate every desk order passes |
| `forge.execution.oms` / `paper` | the existing simulated book and its clearance check |
| `forge.hedgefund.audit.AuditLog` | every intent, command and refusal |
| `forge.contracts` (`FrozenModel`, `stable_id`, `content_hash`) | identity and hashing |
| `forge.workstation` | real panels in real user-owned workspaces |
| `forge.modes` | Prop Desk sections and the AI permission policy |
| `forge_api.actions` | one bounded registry; no parallel verbs |

## 4. New modules

| Module | Responsibility |
|---|---|
| `propdesk/identity.py` | Provider / Platform / ExecutionVenue / DataFeed / BrokerConnection / Account / AccountCapability / AccountKey |
| `propdesk/credentials.py` | secret handling: references, redaction, resolution from environment; no secret is ever persisted |
| `propdesk/instruments.py` | instrument, contract, provider symbol, mapping policies, rounding, roll windows |
| `propdesk/orders.py` | order and position state machines, event ledger, idempotency, brackets/OCO |
| `propdesk/fabric.py` | the adapter interface, rate budget, priority queue, outbox, connection lifecycle, health |
| `propdesk/adapters/` | `simulated` (executes), `rithmic` / `tradovate` / `projectx` (declared, refuse) |
| `propdesk/reconcile.py` | broker-authoritative reconciliation and divergence taxonomy |
| `propdesk/copy.py` | leader, follower, copy group, sizing, copy health |
| `propdesk/policy.py` | prop-firm permissions and four-valued compatibility |
| `propdesk/allocation.py` | strategy health, feasibility, deterministic allocator, advisory clamp |
| `propdesk/news.py` | economic-calendar provider abstraction; manual and FRED providers |
| `propdesk/desk.py` | the funnel: compatibility → prop rules → gate → fabric |
| `propdesk/store.py` | persistence |

## 5. Non-negotiables carried through

* No broker SDK is imported; `LIVE_EXECUTION_AVAILABLE` stays `False`.
* Only the simulated adapter executes. The three provider adapters declare
  their capability surface from the dossier and refuse every command with the
  reason.
* `Compatibility` is four-valued: `ALLOWED`, `BLOCKED`, `UNKNOWN`,
  `REQUIRES_CONFIRMATION`. `UNKNOWN` is never permission.
* AI may recommend an allocation. The advisory layer can only reorder inside the
  deterministic feasible set and reduce size; it can never add a candidate or
  raise a size.
* News data may add a restriction. It can never remove one.
* No VPS, and no hosting provider named anywhere in the code.

## 6. Economic-calendar research (task §10)

**Forex Factory: unsuitable.** No official or documented public API exists, and
`https://www.forexfactory.com/tos` returns HTTP 403 to an automated fetch — the
site actively blocks non-browser access. Every "Forex Factory API" on the market
is a third-party scraper. AlgoForge therefore does not scrape it, does not
bundle a scraped dataset, and does not ship a key.

**What is shipped instead**, behind `CalendarProvider` so the source can be
replaced:

| Provider | Status | Auth |
|---|---|---|
| `ManualCalendarProvider` | implemented and default | none; operator-entered or file-imported |
| `FredReleaseCalendar` (`api.stlouisfed.org/fred/releases/dates`) | implemented; refuses without a key | free registered `FRED_API_KEY` |

FRED is the strongest legitimate option: an official Federal Reserve Bank of
St. Louis API, documented, with `include_release_dates_with_no_data=true`
returning *scheduled future* release dates, and terms of use that permit this
use with a required attribution notice (carried in `news.FRED_ATTRIBUTION`).

Further first-party schedules — BLS (CPI, PPI, Employment Situation), BEA (GDP,
PCE), Census (retail sales), the Federal Reserve's own FOMC calendar — are named
in `news.SUGGESTED_SOURCES` as the next providers to implement. They are HTML/ICS
rather than JSON APIs, so they are a parsing job rather than a client, and
nothing pretends they are wired.
