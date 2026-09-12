# OpenTerminal Integration — Final Report

Branch `feat/openterminal-workstation-integration`, five commits from `a7d9893`.

---

## Executive summary

Selected product and UX principles from **OpenTerminal** (MIT) integrated into
AlgoForge's existing architecture. **No OpenTerminal code was copied.** The two
systems disagree about what data is *for* — OpenTerminal's own README calls its
sources "public endpoints, not officially licensed data feeds… delayed or
indicative, not execution-grade", while AlgoForge's entire validation ladder
exists to decide whether evidence is admissible. Several of OpenTerminal's best
engineering patterns are therefore right in shape and wrong in policy, and those
are the ADAPT rows: the mechanism is kept and the silence is removed.

Four things shipped, each closing a gap that existed before:

1. **Campaign lifecycle entered the action registry.** It had thirty-odd HTTP
   routes and no presence in the registry, so the interface could start a
   campaign and the assistant could not — not because it was denied, but
   because the verb did not exist in the only vocabulary it has.
2. **The workstation gained a context**, and `link_group` — a badge the
   interface drew over a behaviour no code implemented — became true.
3. **The palette runs commands** through that same registry.
4. **Sources gained health, freshness and tiers**, so "which source answered,
   how old is this, and may it be evidence" are answerable.

Two real bugs were found and fixed along the way, neither of them in scope:
`restore()` silently discarded a workspace's sidebar, and a research worker
could silently un-resume a campaign the operator had just resumed.

**This is not a finished product.** Section 15 says plainly what is
production-ready and what is not, and section 14 lists what remains. The largest
honest gap: the freshness envelope and tier ladder are built and tested but
**nothing on the market-data read path consumes them yet**.

---

## What OpenTerminal contributed

| Concept | Verdict | Landed as |
|---|---|---|
| Per-widget `linked` flag resolved at render | **ADOPT** | `forge.workstation.context` |
| Provider health counters (`tracked`) | **ADOPT** | `forge.data.services.ServiceRegistry` |
| Provider status in persistent chrome | **ADOPT** | `ServiceHealth` beside `HealthMatrix` |
| Keyboard composition of the workspace | **ADOPT (partial)** | palette only; see §14 |
| Stale-while-revalidate | **ADAPT** | `Served`, with freshness in the type |
| `withFallback` | **ADAPT** | `chain`, reporting which link served |
| Symbol search | **ADAPT** | `search_instruments` over the real catalogue |
| Scoped AI context | **ADAPT** | **not done**; see §14 |
| Economic calendar | **ADAPT** | **not done**; AlgoForge's is stronger, see §14 |
| Forex Factory as a source | **REJECT** | already investigated and refused here |
| Reverse-engineered vendor endpoints | **REJECT** | no SLA, no revision policy, G0 exists to refuse them |
| Naive market-open clock | **REJECT** | wrong on every holiday; that is fabricated state |
| `localStorage` workspace persistence | **REJECT** | would be the second workspace system |
| Equity screener / heatmap / options / crypto | **REJECT** | not AlgoForge's instrument, not licensed |
| "No API key required" as a goal | **REJECT** | adopted as a floor, not a goal |

Full reasoning per row: `docs/OPENTERMINAL_PATTERN_AUDIT.md`.

### The pattern worth naming

OpenTerminal's `useWidgetSymbol()` is three lines and is the correct solution to
a problem AlgoForge had solved wrongly:

```ts
return widget.linked ? active : widget.symbol ?? active
```

Resolution **at render**, never a write. The peer-group alternative — when a
panel changes symbol, write it to its neighbours — is what "link group" sounds
like it should mean, and it destroys the pinned symbol the first time any group
member moves, with nothing left to restore it from.

---

## What AlgoForge already had

Worth stating, because most of the brief's request list was already built and
the work was to reach it, not to rebuild it:

- **A command palette** — 166 lines, `Ctrl/Cmd+K`, six record groups, keyboard
  cursor management. Navigation-only.
- **A 129-action registry** behind one registration path, one dispatch, and
  `permissions.evaluate()` — first-match-first, with `protected` denied to an AI
  actor in every mode and stance.
- **Server-side versioned workspaces** with a 49-destination sidebar catalogue,
  export/import, history and restore.
- **`FrontierState`** already carrying all nine states §23 asks for; `RuntimeState`,
  `SkipKind` and `AgentRole` from the previous phase.
- **`InstrumentCatalogue`** — 12 futures roots with multipliers, tick values,
  a `verified` flag and `source_note` provenance.
- **`data/health.py`** — dataset integrity measured from the archive, with gap
  classification against the session break and the weekend.
- **An economic calendar** with stronger provenance than OpenTerminal's:
  `EconomicEvent` carries `source`, `source_url`, `retrieved_at`,
  `time_is_approximate`, and an `Impact.UNKNOWN` a blackout deliberately does
  not fire on. `FredReleaseCalendar` refuses without a key rather than falling
  back.
- **`dossier.py`** — assembles, never computes; reports `available: false` with
  a reason rather than a section of zeroes.

---

## Architecture changes

### `packages/forge/workstation/context.py` (new)

`WorkstationContext` — six facets, all optional, empty meaning unset and never
inferred. Held per link group on the `Workspace` in a `contexts` column added by
the store's existing ALTER TABLE migration. `resolve(panel, contexts)` is pure
and writes nothing; it reports the symbol, the timeframe, the group, and
**where each value came from**.

### `packages/forge/data/freshness.py` (new)

`Tier` (EXECUTION_GRADE → UNAVAILABLE, ranked), `Freshness`
(FRESH / REFRESHING / STALE / DEGRADED / UNAVAILABLE), and `Served[T]` carrying
provider, tier, freshness and both timestamps. `admissible(required)` returns
the answer **and the reason**.

### `packages/forge/data/services.py` (new)

`ServiceRegistry` with observed counters; `chain()` reporting which link served
and marking a cross-tier descent DEGRADED; `ABSENT_CAPABILITIES` naming what
this build does not have.

### `apps/api/forge_api/campaigns.py`

Lifecycle moved out of the route closures onto `CampaignService`, with the
engine hooks attached to the service rather than captured in a closure. Routes
became four-line exception translations. `UnknownCampaign` subclasses
`CampaignError` so existing handlers keep working while 404 stays separable
from 409.

### Registry: 129 → 150 actions

15 campaign, 5 context/instrument, 1 `data_health`.

---

## Security review

- **No secret is read, returned, logged or rendered by any new code.**
  `credential_present()` answers one bit — whether a credential exists — and
  returns nothing else. The only new code that touches credentials is the
  `UNCONFIGURED` classification in `data_health`.
- **A provider's error text is truncated to 200 characters and never formatted
  into a model prompt or an audit row.** A provider error can carry a signed URL.
- **No new action is `protected`, and none is `HIGH` risk.** Both are asserted
  as properties (`test_no_campaign_verb_is_protected_and_none_is_high_risk`), so
  a campaign verb that later becomes a route to an execution control is noticed.
- **The confirmation boundary was not widened.** `Actions.call` refuses anything
  above `SAFE` without `confirmed`, and the HTTP action endpoint cannot pass it.
  It would have been easy to add `confirmed` to `ActionRequest` to let the
  palette offer `archive_campaign`; that would let any HTTP caller set it, which
  is exactly the escape route the flag exists to close. The palette offers only
  SAFE verbs instead.
- **AI permissions are unchanged.** Every new action goes through
  `permissions.evaluate()` like every existing one.
- **G0–G13 is untouched.** `git diff origin/main...HEAD -- packages/forge/judge/`
  is empty.
- Instrument names are validated against the catalogue before being stored as
  context, so a context cannot carry a symbol nothing can size from.

---

## Data and provider limitations

Stated because the surfaces state them, and they should agree:

- **No headline news feed exists in this codebase.** The module named `news`
  models scheduled economic events. Nothing was fabricated to fill the gap, and
  Data Health names the absence rather than omitting the section.
- **No source in this build is execution-grade.** There is no venue feed and no
  order path. A test asserts no provider claims the tier.
- **The FRED calendar is unavailable without `FRED_API_KEY`**, and says so with
  the URL that issues free keys. It does not fall back.
- **Databento is a paid, per-request source.** `/bars` returns 409 for a
  dataset that has not been downloaded, and the panel shows an honest empty
  state rather than a blank chart.
- Health counters are **observations since this process started**, not uptime.
  Every row says so.

---

## Testing

| | Before | After |
|---|---|---|
| Backend (`pytest`) | 2,498 | **2,590** |
| Frontend (`vitest`) | 86 | **117** |

The backend figure is the whole suite run to completion with **zero failures,
zero errors and zero skips** — verified by counting result characters against
the collected count (2,590 dots, 2,590 collected) rather than by reading a
summary line, because pytest's summary was twice cut by process teardown in
this environment and a missing summary is not evidence of anything.

**121 new tests.** Backend by file:

| File | Tests | Covers |
|---|---|---|
| `tests/data/test_freshness_and_services.py` | 28 | tiers, admissibility, freshness, health states, fallback chains |
| `tests/workstation/test_context.py` | 21 | resolution, non-mutation, bounds, persistence, export |
| `tests/api/test_campaign_actions.py` | 19 | the verbs, one path across two surfaces, risk properties |
| `tests/api/test_context_actions.py` | 19 | instrument search, context refusals, data health |
| `tests/research/test_campaign_concurrency.py` | 3 | the lost update, driven deliberately |

Frontend: 9 palette, 8 source health, 7 panel resolution, 6 context bar, 1 shell.

### Invariants pinned as properties (§58)

- AI cannot call a protected action — pre-existing, unchanged.
- No campaign verb is protected or HIGH risk.
- Unavailable data is never admissible, at any tier.
- A lower tier is blocked, not warned.
- Every state in `NOT_EVIDENCE` is refused (parametrised over the set, so
  adding a state to the set adds a test).
- A service nobody has called is not healthy.
- Nothing in this build claims EXECUTION_GRADE.
- The workspace group ceiling fires on a **mutation**, not only at construction.
- Setting a context writes nothing to any panel.
- A concurrent counter write cannot revert a status change.

### The regression tests were confirmed to fail without the fix

Removing the lock from `record()` loses 13 of 100 increments. That was measured,
not assumed.

---

## Visual and performance QA

Four widths on the running application (1920 / 1440 / 1024 / 420):
**no horizontal overflow anywhere, no console errors, no page errors.**

Measured with 60 campaigns registered:

| | |
|---|---|
| Initial load + entering a workspace | 1,289 ms, 80 requests |
| Palette open | 229 ms |
| Palette search across 60 campaigns | 39 ms, 12 rows |
| Data Health first render | 831 ms, 6 requests |
| Idle 20 s | 5 requests (pre-existing 5 s activity + 15 s health polls, plus a 30 s refresh) |
| `data_health` | 28 ms median |
| `describe_context` | 5 ms median |

End-to-end on the real application: a panel pinned to MNQ and linked to a
context on NQ displayed **NQ**; an unlinked neighbour kept **ES**; unlinking
brought **MNQ** straight back out of the record untouched. A campaign was
created, started from the palette, and confirmed running with its agent crew
deployed, all through the registry.

---

## Bugs found

Four, none of them in the brief:

1. **`restore()` discarded the sidebar.** A version records the layout;
   `version()` rebuilt a workspace from those columns alone, and `restore()`
   saved whatever it returned. An operator restoring yesterday's panel
   arrangement silently lost the rail they had spent the afternoon building.
2. **A worker could un-resume a campaign.** `save` writes every column and the
   read-modify-write pairs were not locked, so a worker holding a row read
   before a pause wrote `status='paused'` over the operator's resume. The same
   race was losing experiment counts.
3. **Typing a ticker offered to start a campaign** — the palette's fixed group
   order put a campaign command above the instrument.
4. **A panel header disagreed with its own body** — "MNQ 5m" over "no archive
   for NQ".

Plus the shipped fake feature the audit set out to find: the link badge.

---

## Known risks

- **The freshness envelope is not enforced anywhere yet** (§14). Until a reader
  on the market-data path calls `admissible()`, the ladder describes a
  capability rather than a behaviour in force. Someone reading
  `DATA_PROVIDER_ARCHITECTURE.md` could reasonably believe otherwise, which is
  why both that document and this one say so explicitly.
- **Service health counters are per-process.** They reset on restart. Labelled,
  but a reader who ignores the label will misread them.
- **Contexts are not exercised by every panel kind.** Chart panels resolve;
  research panels ignore the context entirely, which is correct for now but
  means "select NQ and the whole workstation follows" is not yet true.
- **The group ceiling is 8 and the context is per-workspace**, so a very large
  arrangement with many independent subjects is not supported. No evidence yet
  that anyone wants one.

---

## Production readiness

Honestly, per component:

| Component | State |
|---|---|
| Campaign verbs in the registry | **Production-ready.** Same code path as the tested HTTP routes, 19 tests, permissions applied. |
| The lost-update fix | **Production-ready.** Reproduced, fixed, regression-tested, confirmed to fail without the fix. |
| Workstation context and resolution | **Production-ready for chart panels.** Model, storage, migration, export and resolution are tested and verified live. Other panel kinds ignore it. |
| Palette commands | **Production-ready** within its deliberate limit: SAFE verbs only. |
| Instrument search | **Production-ready.** Reads the real catalogue and marks unverified specifications. |
| Service health and Data Health | **Production-ready as an observability surface.** Truthful about what it does and does not know. |
| Freshness, tiers, fallback chains | **Not in force.** Built, tested, unconsumed. Scaffolding until a reader uses it. |
| Scoped AI context | **Not started.** |
| Calendar and news surfacing | **Not started.** |
| Keyboard map beyond the palette | **Not started**, deliberately — see §14. |

Nothing here is claimed as "fully integrated". The workstation is measurably
more coherent — one registry reachable from four surfaces, one context, one
health surface — and three of the brief's ten phases are genuinely untouched.

---

## Remaining work

In the order I would do it:

1. **Consume `Served` on the market-data read path.** Make `MarketService`
   return envelopes and make the backtest path call `admissible()` before using
   bars. This is what turns the tier ladder from a description into a control,
   and it is the single highest-value thing left.
2. **Scope the assistant's context** (§26). `assistant.py:context()` returns a
   fixed global blob — every template name, up to 60 strategies, 25 activity
   events — regardless of the screen. The workstation context now exists to
   scope it against.
3. **Lift the calendar out of Prop Desk** (§15, §16). The implementation is
   good and is reachable only through `propdesk_news` and one panel kind.
   Event-aware research needs it at workstation level.
4. **The keyboard map** (§5), after a real conflict check. `Cmd+Shift+P` is
   Firefox's private window; `Alt+digit` switches tabs on two platforms. Every
   shortcut also needs a visible control first, since a shortcut may never be
   the only route to a capability.
5. **Context for research panels**, so selecting a campaign moves the frontier,
   agent monitor and timeline together.
6. **Market context and regime** (§17, §18) — needs its own investigation
   before anything is promised, and may honestly be UNAVAILABLE on this data.

---

## Recommended next phase

**Make the tier ladder bite.** Item 1 alone changes the system's behaviour
rather than its surface area, and it is the difference between a workstation
that *displays* data quality and one that *enforces* it. Everything else on the
list is a surface; that one is a control.

Second, item 2 — the assistant is the largest remaining source of unscoped
data flow, and the context it needs now exists.

I would not start any new OpenTerminal-derived work until both are done. The
audit's REJECT list is long for a reason, and what remains on the ADAPT list is
smaller than what has already been built but not yet wired.
