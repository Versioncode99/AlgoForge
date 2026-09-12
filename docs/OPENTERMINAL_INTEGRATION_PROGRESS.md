# OpenTerminal Integration — Progress

**Purpose.** This file is the handover record. If a session is interrupted, the
next one continues from here. It is updated continuously, not at the end.

**Branch.** `feat/openterminal-workstation-integration`, cut from `main` at
`a7d9893`.

**Rules in force for this work** (from the brief): audit before changing;
preserve and extend the existing architecture rather than duplicating it; no
fake features; G0–G13 stay authoritative; no secrets anywhere; no force push;
no merge to main; logical commits.

---

## Status

| Phase | State | Notes |
|---|---|---|
| 0 — Audit | **done** | `docs/OPENTERMINAL_PATTERN_AUDIT.md` |
| 1 — Architecture | **done** | workstation context; campaign verbs in the registry |
| 2 — Workstation UX | **done** | palette commands, instrument search, context bar, panel resolution |
| 3 — Data / market | **partly done** | freshness, tiers, service health, Data Health shipped; **nothing consumes `Served` yet**; calendar/news surfacing not started |
| 4 — Research | **partly done** | campaign verbs in the registry; research panels do not read the context |
| 5 — Prop Desk | **not started** | nothing in this phase touched it |
| 6 — AI | **not started** | assistant context still a fixed global blob |
| 7 — Visual / performance QA | **done** | four widths, no overflow, no console errors, measured under load |
| 8 — Verification | **done** | 2,590 backend + 117 frontend, ruff clean, mypy clean |
| 9 — Documentation | **done** | eight documents |

## Baselines measured before any change

- Frontend: **86 tests pass**, 11 files (`npx vitest run`, 11.6 s).
- Python: **2,498 tests, no failures** (`pytest -q`, ~25 min). The summary line
  was cut by a process teardown; every result character in the output is a dot,
  and the run is repeated at verification time.
- Working tree clean at branch creation.

---

## Phase 0 — Audit (done)

Full findings: `docs/OPENTERMINAL_PATTERN_AUDIT.md`.

Three findings that reordered the plan:

1. **A command palette already exists** (`apps/web/src/components/CommandPalette.tsx`,
   166 lines, bound at `App.tsx:225`). It searches six record types and is
   navigation-only. §4 is therefore "give the existing palette the action
   registry", not "build a palette".

2. **Campaign lifecycle is absent from the action registry.** `campaigns.py`
   serves 30+ routes; none of the 129 registered actions touches a campaign.
   The UI can start a campaign; the AI and the palette have no verb for it.
   This is the largest §4 gap and the highest-value item.

3. **`link_group` is a label attached to nothing.** `Panel.link_group`'s
   docstring claims panels "follow each other's symbol and timeframe";
   `Workspace.linked()` only writes the tag, nothing reads it, and
   `Workspace.tsx:310` renders it as a badge. A shipped fake feature by §61.
   OpenTerminal's `useWidgetSymbol()` is the correct shape for fixing it.

Decisions: 4 ADOPT, 5 ADAPT, 6 REJECT. The REJECT list includes Forex Factory
(AlgoForge already investigated and rejected it; OpenTerminal scrapes it with a
spoofed User-Agent), reverse-engineered vendor endpoints, the naive
market-open clock, `localStorage` workspace persistence, the equity-terminal
widget set, and "no API key required" as a design goal.

Not promised, and why, in §6 of the audit: headline news (no feed exists —
`news.py` models scheduled events despite its name), live market regime (needs
its own investigation), Copy Trader (already exists; OpenTerminal has no
execution layer to contribute).

### Verified by reading the implementation, not the docs

- 129 actions, single `_add` registration path, closed set.
- `permissions.evaluate()` denies every `protected` action to an AI actor in
  every mode and stance, first-match-first, one named reason per refusal.
- `FrontierState` already carries all nine states §23 asks for.
- `RuntimeState`, `SkipKind`, `AgentRole` from the previous phase are present.
- `dossier.py` reports `available: false` with a reason rather than zeroes.
- `InstrumentCatalogue`: 12 roots, 8 `verified=True`, 4 `verified=False` with
  provenance notes.
- `propdesk/news.py` calendar has stronger provenance than OpenTerminal's and
  refuses rather than falling back when `FRED_API_KEY` is absent.
- `data/health.py` measures datasets from the archive; there is **no**
  service/provider health anywhere.
- `assistant.py:context()` returns a fixed global blob regardless of screen.

---

## Open questions carried forward

- `link_group`: give it the behaviour its badge claims, or remove the badge?
  The audit recommends replacing the promise with resolution-at-render
  (§2.2) and keeping `link_group` only if it earns its keep.
- Which keyboard shortcuts survive a real conflict check (Alt+digit and
  Cmd+Shift+P both collide on some platforms).
- Whether a live market-context panel is supportable at all on the data
  AlgoForge has, or whether it must ship as UNAVAILABLE.


---

## Phase 1 — Architecture (done)

**Campaign verbs in the action registry.** The lifecycle moved out of the route
closures onto `CampaignService`; `build_campaign_router` now attaches the engine
hooks to the service, the routes became four-line exception translations, and
fifteen actions call the same methods. `UnknownCampaign` subclasses
`CampaignError` so existing handlers keep working while a 404 can still be told
from a 409.

Registry went **129 → 149 actions**. None of the campaign verbs is `protected`;
`archive_campaign` is `CONFIRM`. Asserted as a property in
`tests/api/test_campaign_actions.py`.

**Workstation context** — `packages/forge/workstation/context.py`. Instrument,
timeframe, dataset, campaign, strategy and account, held per link group on the
workspace and resolved at render time. `resolve` is pure and writes nothing.

This is what makes `link_group`'s badge true. The peer-group alternative — write
the new symbol across the group — destroys a pinned symbol and cannot be undone.

Storage: a `contexts` column via the existing `_ADDED` ALTER TABLE migration,
default `'{}'`. Mutations rebuild the model rather than `model_copy`, so the
group ceiling runs on every change.

## Phase 2 — Workstation UX (partly done)

**The palette runs commands.** Groups are now Commands, Instruments, Views,
Campaigns, Strategies, Experiments, Runs, Constraints, Datasets. Commands go
through `POST /actions/<name>`.

Only verbs that will actually run are offered. The HTTP action endpoint cannot
pass the operator's confirmation — `ActionRequest` carries `arguments` and
nothing else — so a `CONFIRM` verb in the palette would be a control drawn as
working that always refuses. **This boundary was deliberately not widened.**

Lifecycle rows are offered by status: running → pause/stop, paused → resume,
created/stopped → start, archived → nothing.

**Instrument search** over the real `InstrumentCatalogue`. Unverified
specifications say so in the row, because the multiplier is the number a
position would be sized from and four of the twelve are unverified.

### Still to do in Phase 2
- Context bar facets (instrument / campaign / strategy / account) in `App.tsx`.
- The documented keyboard map (§5), after a real conflict check.

## Phase 3 — Data (partly done)

`packages/forge/data/freshness.py` — `Tier` (EXECUTION_GRADE → UNAVAILABLE,
ranked), `Freshness` (FRESH / REFRESHING / STALE / DEGRADED / UNAVAILABLE), and
`Served`, an envelope carrying the provider, the tier, the freshness and both
timestamps. `Served.admissible(required)` returns the answer **and the reason**.

`packages/forge/data/services.py` — `ServiceRegistry` with observed counters, a
`chain` that reports which link served and marks a cross-tier descent
`DEGRADED`, and `ABSENT_CAPABILITIES` naming what this build does not have.

Wired: `data_health` action composing the measured dataset matrix, the service
registry, calendar availability, the absent capabilities and the tier ladder.
`ServiceHealth.tsx` renders it beside the existing `HealthMatrix`.

Real output on a machine with no keys: databento and fred `UNCONFIGURED`,
crypto-public `NOT_OBSERVED`, three absent capabilities named.

### Still to do in Phase 3
- Nothing yet *consumes* `Served` on the market-data read path. The envelope,
  the chain and the tier ladder exist and are tested; `MarketService` still
  returns bare frames. **This is scaffolding until that lands** and is reported
  as such.
- Calendar and news surfacing out of Prop Desk (§3.3 of the audit).


---

## Phase 7-9 — QA, verification, documentation (done)

### Verification

| | Before | After |
|---|---|---|
| Backend | 2,498 | **2,590** |
| Frontend | 86 | **117** |

`ruff check .` clean. `mypy` clean, 187 source files. G0–G13 untouched
(`git diff origin/main...HEAD -- packages/forge/judge/` is empty).

### Visual QA

1920 / 1440 / 1024 / 420 on the running application, across the shell, the
palette, the palette with a query, and Data Health. **No horizontal overflow at
any width. No console errors. No page errors.**

Four faults found by looking rather than reasoning, all fixed and all now
pinned by tests:

1. Typing `NQ` put "Start campaign · NQ Momentum" above the instrument.
2. MNQ sorted above NQ.
3. A panel header read "MNQ 5m" over a body reading "no archive for NQ".
4. A resumed campaign un-resumed itself (the lost update).

### Performance

Measured with 60 campaigns: palette search 39 ms, `data_health` 28 ms median,
`describe_context` 5 ms median, Data Health first render 831 ms, five requests
in 20 idle seconds.

### Documents

`OPENTERMINAL_PATTERN_AUDIT.md`, this file, `WORKSTATION_ARCHITECTURE.md`,
`DATA_PROVIDER_ARCHITECTURE.md`, `CONTEXT_ARCHITECTURE.md`,
`COMMAND_PALETTE_ARCHITECTURE.md`, `UX_IMPLEMENTATION_REPORT.md` (part two
appended), `FINAL_OPENTERMINAL_INTEGRATION_REPORT.md`.

---

## For the next session

The three things to do next, in order, with the reasoning in the final report's
"Recommended next phase":

1. **Consume `Served` on the market-data read path.** Everything for the tier
   ladder exists and is tested; nothing calls `admissible()`. Until that lands
   the ladder describes a capability, not a control in force, and both the
   provider document and the final report say so.
2. **Scope the assistant's context.** `assistant.py:context()` still returns a
   fixed global blob regardless of the screen. The workstation context now
   exists to scope it against.
3. **Lift the calendar out of Prop Desk.** The implementation is good and is
   reachable only through `propdesk_news` and one panel kind.

Untouched by this phase and not claimed: Prop Desk (§28-§33), AI (§26, §53),
research panel context, market regime (§17-§18), the keyboard map beyond the
palette (§5 — deliberately, pending a real conflict check).
