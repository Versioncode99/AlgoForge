# OpenTerminal Pattern Audit

**Phase 0. No feature changes were made while writing this document.**

What this is: an inspection of the actual AlgoForge implementation on `main`
(`a7d9893`), an inspection of the actual OpenTerminal repository
(`ErTasselli/OpenTerminal`, MIT, cloned and read at
`server/src/`, `web/components/`, `web/store/`), and a decision — ADOPT, ADAPT
or REJECT — for every OpenTerminal concept that touches something AlgoForge
does or should do.

What this is not: a plan to make AlgoForge look like OpenTerminal. The two
systems disagree about what data is *for*. OpenTerminal's own README says its
sources are "public endpoints, not officially licensed data feeds — treat
prices as delayed/indicative, not execution-grade". AlgoForge's entire
validation ladder exists to decide whether evidence is admissible. Several of
OpenTerminal's best engineering patterns are therefore right in shape and
wrong in policy, and those are the ADAPT rows: the mechanism is kept and the
silence is removed.

---

## 0. What was actually inspected

### AlgoForge

Read, not assumed:

| Area | Files | Finding |
|---|---|---|
| Action registry | `apps/api/forge_api/actions.py` (170 KB) | 129 actions, one `_add` helper, closed set |
| Permissions | `packages/forge/modes/permissions.py` | `evaluate()`, first-match-first, `protected` denies AI unconditionally |
| Workspaces | `packages/forge/workstation/{models,store,sidebar,templates}.py` | Server-side, versioned, 49-destination sidebar catalogue, 12-column grid |
| Panels | `packages/forge/workstation/models.py` | `PanelKind` closed set, 40 kinds; `EXECUTION_PANELS` boundary |
| Panel rendering | `apps/web/src/components/PanelBody.tsx` | 29 kinds rendered; the rest fall to an honest `NotBuilt` |
| Research fabric | `packages/forge/research/` (28 modules) | `FrontierState`, `RuntimeState`, `SkipKind`, `AgentRole`, orchestrator |
| Campaigns API | `apps/api/forge_api/campaigns.py` | Full lifecycle over HTTP: 30+ routes |
| Validation | `packages/forge/judge/` | G0–G13 present in `engine.py`/`explain.py` |
| Strategy evidence | `apps/api/forge_api/dossier.py` | Assembles, never computes; `available: false` with a reason |
| Instruments | `packages/forge/propdesk/instruments.py` | 12 CME/COMEX/NYMEX/CBOT roots, `verified` flag, `source_note` provenance |
| Datasets | `apps/api/forge_api/market.py` | 11 datasets, `authority` TRUTH/FIXTURE, cost notes |
| Dataset health | `packages/forge/data/health.py` | Measured from the archive; gap classification with session/weekend/closure exclusions |
| Calendar | `packages/forge/propdesk/news.py` | `EconomicEvent` with provenance, `ManualCalendar` + `FredReleaseCalendar`, `Impact.UNKNOWN` |
| Shell | `apps/web/src/App.tsx` | Rail + context bar + palette + switcher; hash routing |
| Palette | `apps/web/src/components/CommandPalette.tsx` | 166 lines; six record groups |
| Assistant | `apps/api/forge_api/assistant.py` | Bounded tool loop over the action registry |

Baseline before any change: **86 frontend tests pass** (`vitest`, 11 files).
The Python suite baseline is recorded in the progress document.

### OpenTerminal

Cloned at depth 1 and read: `server/src/cache.ts`, `server/src/providers/registry.ts`,
`server/src/providers/econcalendar.ts`, `server/src/routes/ai.ts`,
`web/store/terminal.ts`, `web/components/{Terminal,TopBar,CommandPalette}.tsx`,
plus the README and the provider list. 52 TS/TSX files total — it is a small,
legible codebase, and that is part of why the patterns read clearly.

**Licensing.** MIT. Copying code would be permissible with attribution. This
audit nevertheless proposes copying **no code**: the two systems' data policies
differ enough that every candidate file would need rewriting anyway, and
`THIRD_PARTY_NOTICES.md` should not grow an entry for something not used.
Patterns are not copyrightable; the decisions below are about shape.

---

## 1. Findings that changed the plan

Three things found by reading the code rather than the docs. They are listed
first because they reorder everything after them.

### 1.1 AlgoForge already has a command palette — and it cannot run commands

`apps/web/src/components/CommandPalette.tsx` exists, is bound to `Ctrl/Cmd+K`
in `App.tsx:225`, and searches six record types (views, strategies,
experiments, runs, constraints, datasets). It is genuinely good, and it is
**navigation-only**: every result's `onRoute(route)` sets a hash. There is no
path from the palette to an action.

OpenTerminal's palette is *narrower* (symbol search only) but does one thing
AlgoForge's does not: picking a result **establishes context** and a modifier
key (`Shift+Enter`) performs a second, different operation on the same result.

So §4 of the brief is not "build a command palette". It is "give the existing
palette the action registry". That is a smaller and safer change than it
looked.

### 1.2 Campaign lifecycle is absent from the action registry

`campaigns.py` implements create / start / stop / pause / resume / duplicate /
archive / restore / priority / rename / deploy-agents over HTTP. None of it
appears among the 129 registered actions.

The consequence is an asymmetry that §4 explicitly forbids: the UI can start a
campaign, and **the AI cannot** — not because it is denied, but because the
verb does not exist in the only vocabulary it has. The palette cannot either.
Three surfaces, two of them mute.

This is the single highest-value integration in the whole brief, and it is
deletion of a gap rather than addition of a system: the service layer already
exists and is already tested over HTTP.

### 1.3 `link_group` is a label attached to nothing

`Panel.link_group` is declared in `packages/forge/workstation/models.py:139`
with the docstring:

> Panels sharing a link group follow each other's symbol and timeframe.

They do not. `Workspace.linked()` (`models.py:419`) tags the named panels with
a group string and returns. `store.py` never reads it. The only other
occurrence in the entire frontend is `Workspace.tsx:310`, which **renders the
group name as a badge on the panel header**.

So the product shows the operator a badge asserting a link, the docstring
asserts a behaviour, the action `link_panels` asserts it in its summary — and
nothing propagates anything. By §61 this is a fake feature, and it is one
AlgoForge shipped. It is recorded here rather than quietly fixed because the
brief asked for exactly this kind of finding, and because OpenTerminal happens
to contain the correct implementation of it (§2.2 below).

---

## 2. ADOPT

Concepts taken in substance. In each case AlgoForge has no equivalent.

### 2.1 Provider health as a first-class, observable record

**OpenTerminal concept.** `server/src/providers/registry.ts`: `tracked(name, fn)`
wraps every provider call and accumulates `{ok, failed, lastLatencyMs,
avgLatencyMs, lastError, lastSuccess}`. `allStats()` exposes it; `/api/status`
serves it; `TopBar.tsx` renders it.

**AlgoForge equivalent.** None. `packages/forge/data/health.py` measures a
*dataset on disk* — rows, gaps, OHLC consistency — which is a different
question and an excellent answer to it. Nothing measures whether a *service*
answered, how fast, or when it last worked. `forge_api/providers.py` is the
**model** provider layer (OpenCode/DeepSeek), not market data.

**Existing component to extend.** `packages/forge/data/` gains a service-health
registry; `propdesk/news.py:Availability` is the right existing shape for
"can this be used right now, and what does it need" and should be generalised
rather than re-invented.

**Required change.** A `ServiceHealth` record per provider with observed
counters, and a Data Health surface that composes it with the existing
`HealthReport` for datasets. Both, side by side, answering WHAT / WHY /
IMPACT / REMEDY per §13.

**Why it improves AlgoForge.** The existing context bar says `DATA REAL` or
`DATA UNKNOWN` from `/health` and nothing else. An operator cannot currently
find out *which* source is degraded or when it last worked.

**Risks.** Health counters are per-process and reset on restart; they must be
labelled as observations since start, not as uptime. A provider never called
must read `NOT_OBSERVED`, never `HEALTHY` — the zero-value trap that
`dossier.py` already avoids for evidence.

**Tests.** Counters advance on success/failure; a never-called provider reports
`NOT_OBSERVED`; the surface distinguishes "failed" from "never tried".

### 2.2 A global active instrument, with per-panel opt-in resolved at render

**OpenTerminal concept.** `web/store/terminal.ts`: one `activeSymbol`, each
widget carries `linked: boolean` and an optional own `symbol`, and
`useWidgetSymbol()` resolves at render time:

> `return widget.linked ? active : widget.symbol ?? active`

**AlgoForge equivalent.** The broken `link_group` of §1.3, and no global
instrument at all.

**Why OpenTerminal's shape is the right one**, and precisely what §6 asks for
when it says "Do NOT globally mutate every panel's state in destructive ways.
Use explicit context propagation": because the panel's own stored `symbol` is
**never written** when context changes. A linked panel *displays* the active
instrument; unlinking it reveals the symbol it always had. A peer-group model
that mutates panels (which is what `link_group` would have to do) destroys the
pinned symbol the moment a group member changes, and cannot be undone.

**Required change.** Add an instrument context to the workstation — active
instrument resolved against `InstrumentCatalogue` — and replace `link_group`'s
promise with a `follows_context` flag whose resolution happens where the panel
renders. `link_group` itself should either gain the behaviour its badge claims
or lose the badge; it must not keep asserting an untruth.

**Risks.** Changing context must not silently change what a *research* panel is
showing evidence for. Panels bound to an experiment or a campaign are not
instrument-followers and must not be made into them.

**Tests.** Linked panel follows; pinned panel does not; unlinking restores the
pinned symbol unchanged; an unknown instrument is refused, not displayed.

### 2.3 Keyboard composition of the workspace

**OpenTerminal concept.** `Terminal.tsx`: `Alt+1`…`Alt+9` add a widget of a
given type directly.

**AlgoForge equivalent.** `Ctrl/Cmd+K` and `Escape` only (`App.tsx:222–229`).
Panels are added through a picker.

**Required change.** A documented, discoverable, non-conflicting shortcut map,
per §5 — including the brief's own candidates — routed through the action
registry rather than through component callbacks, so a shortcut and a palette
command and an AI call are the same operation.

**Risks.** `Alt+digit` collides with browser tab switching on some platforms;
`Cmd+Shift+P` is the browser's private window on Firefox. Shortcuts must be
chosen against real conflicts and listed in the docs, and none may be the only
route to a capability (§37).

**Tests.** Each shortcut dispatches the named action; none fires while a text
input has focus; the documented list and the implemented map are asserted
equal so they cannot drift.

### 2.4 Provider status in the persistent chrome

**OpenTerminal concept.** `TopBar.tsx` keeps session state, clocks and provider
health permanently on screen at 11px — status is ambient, not a page you visit.

**AlgoForge equivalent.** `.context-bar` in `App.tsx` already does this for
engine/data/strategy counts and is the right host. This is an extension of an
existing component, not a new one.

**Required change.** Extend the context bar with the instrument / campaign /
strategy / account facets §7 asks for, keeping them **visually distinct** so
the workspace-vs-mode error the previous phase fixed is not re-made in a new
form.

**Risks.** Crowding. The bar is already six facts wide and must survive 420px.

---

## 3. ADAPT

Right mechanism, wrong policy for a system that produces evidence.

### 3.1 Stale-while-revalidate — keep the resilience, delete the silence

**OpenTerminal concept.** `server/src/cache.ts`:

```ts
} catch (err) {
  const stale = staleGet<T>(key);
  if (stale !== undefined) return stale;
  throw err;
}
```

On provider failure the last-known value is returned. The caller cannot tell.
The return type of `cached<T>()` is `T` whether the data is one second old or
one week old.

**Why it is right for OpenTerminal and wrong for AlgoForge.** A quote panel
showing a slightly old price is better than a blank panel. A backtest whose
bars silently came from a week-old cache is a fabricated result, and §12 and
§42 both forbid it.

**Required change.** Keep the cache and the fallback; change the *type*. A
value comes back in an envelope carrying `Freshness` — `FRESH`, `REFRESHING`,
`STALE`, `DEGRADED`, `UNAVAILABLE` — with the retrieval timestamp and the
provider that served it. Informational surfaces may render `STALE` with a
marker (§12 permits this explicitly for news, calendar, market context and
instrument metadata). The evidence path may not: an experiment requiring
execution-grade data must refuse a `STALE` or `DEGRADED` envelope rather than
consume it.

**Why this is not over-engineering.** Making staleness part of the type is what
makes "stale data cannot silently masquerade as fresh" (§58) a property the
compiler and the tests can check, instead of a convention.

**Tests.** A fresh read is `FRESH`; a failed refresh over a cached value is
`STALE` and says so; an evidence-grade consumer refuses `STALE`; an
informational consumer renders it with the marker; nothing returns a bare value.

### 3.2 Fallback chains — keep the order, report the descent

**OpenTerminal concept.** `withFallback([[name, fn], …])` returns the first
success.

**The gap.** The caller learns nothing about which provider answered. Quotes
sourced from Nasdaq and quotes sourced from Stooq arrive identically typed and
identically presented. For OpenTerminal, where everything is indicative, that
is coherent. For AlgoForge it is the §11 failure: *"Never silently switch from
high-quality data to low-quality data while pretending nothing changed."*

**Required change.** A fallback chain that is deterministic, ordered, and
**returns which link served the request and at what tier** — the brief's
EXECUTION-GRADE / RESEARCH-GRADE / INDICATIVE / SYNTHETIC / UNAVAILABLE. A
descent that crosses a tier boundary is an event, is logged, and is shown:

> Data source degraded: Databento unavailable. Using fallback source.
> Backtests requiring execution-grade data are blocked.

**Reuse, not replacement.** `ProviderDescriptor.authority` (TRUTH / CONTEXT /
FIXTURE) in `packages/forge/data/models.py` already encodes most of this
distinction and `Dataset.authority` uses it. The tier vocabulary should extend
that existing field's meaning rather than introduce a second, competing axis.

**Risks.** A tier ladder invites a "close enough" substitution. The rule must
be that crossing down a tier blocks the consumers that declared the higher
tier, rather than warning them.

**Tests.** Chain order is deterministic; the serving provider is reported; a
cross-tier descent blocks an execution-grade consumer; the descent is auditable.

### 3.3 Economic calendar — AlgoForge's is better; the surfacing is worse

**OpenTerminal concept.** `providers/econcalendar.ts` fetches Forex Factory's
weekly JSON and backfills `actual` from FRED for a curated set of releases.

**AlgoForge equivalent.** `packages/forge/propdesk/news.py` — and it is
strictly stronger on provenance: `EconomicEvent` carries `source`, `source_url`,
`retrieved_at`, `time_is_approximate` and an `Impact.UNKNOWN` that a blackout
deliberately does not fire on. `FredReleaseCalendar` refuses without a key
rather than falling back. `FRED_ATTRIBUTION` is returned with every response so
a surface cannot render the data without it.

**What to adapt is the placement, not the model.** The calendar is reachable
today only through `propdesk_news` and the `DESK_NEWS` panel — it is a Prop
Desk feature. §15 and §16 want it as a workstation-level concern that research
can condition on. The change is to lift the surface (a panel kind, an action, a
context facet) while leaving the implementation exactly where it is.

**Risks.** Lifting it must not imply broader coverage than FRED + manual entry
actually provides. Without `FRED_API_KEY` the honest state is UNAVAILABLE, and
the panel must say so rather than render empty.

### 3.4 AI context — scope it to the surface

**OpenTerminal concept.** `routes/ai.ts` receives `{activeSymbol, quote}` as a
JSON context block. Tiny, explicit, and about what the user is looking at.

**AlgoForge equivalent.** `assistant.py:context()` returns a **fixed global
blob** every time: strategy count, backtest count, families, every template
name, up to 60 strategies and 25 activity events — regardless of the screen.

**Why OpenTerminal wins here despite being less capable.** AlgoForge's
assistant is far more powerful (a bounded tool loop over 129 permissioned
actions, versus a model that cannot act at all). But §26 is explicit: *"Do not
dump the entire database into every prompt"*, and context must be *scoped,
explicit, auditable, permissioned*. A fixed 60-strategy dump is none of those.

**Required change.** Context assembled from the *current* workstation context —
instrument, campaign, strategy, account — declared explicitly, with what was
included recorded for audit. The action registry and `permissions.evaluate()`
stay exactly as they are: this changes what the model is *told*, never what it
may *do*.

**Risks.** Under-scoping degrades answers. The remedy is that the assistant can
call actions to fetch what it lacks — which is the capability it already has.

### 3.5 Symbol search → instrument search with provenance

**OpenTerminal concept.** Search returns `{symbol, name, exchange, type}` from
TradingView/Yahoo, across the whole equity universe.

**AlgoForge equivalent.** `InstrumentCatalogue` — 12 futures roots, each with
`multiplier`, `tick_size`, `tick_value`, `product_group`, and crucially
`verified: bool` plus `source_note`. Four of the twelve are `verified=False`
with notes like *"common knowledge; flagged for verification"*.

**Required change.** Search the catalogue AlgoForge actually has. Do not invent
instruments (§6). An unverified specification must be marked as such in the
results, because it is the number a position would be sized from.

**Why this is better than OpenTerminal's.** A search that returns an instrument
whose contract specification nobody has checked, without saying so, is how a
sizing error enters at the top of the funnel.

---

## 4. REJECT

### 4.1 Forex Factory as a calendar source

OpenTerminal fetches `https://nfs.faireconomy.media/ff_calendar_thisweek.json`
with `headers: { "User-Agent": "Mozilla/5.0" }`.

AlgoForge already investigated this exact source and rejected it, and the
reasoning is in `propdesk/news.py`'s module docstring: no documented public
API, the terms page returns HTTP 403 to automated requests, and every
"Forex Factory API" on the market is a third-party scraper. The spoofed
User-Agent in OpenTerminal's implementation is itself the evidence — code does
not need to claim to be Mozilla to read a feed it is welcome to read.

**Rejected.** The existing decision stands, and this audit is a second,
independent confirmation of it rather than a reason to revisit it.

### 4.2 Reverse-engineered vendor endpoints as data sources

The Nasdaq quote/chart/options APIs and the TradingView scanner API are
undocumented front-end endpoints. OpenTerminal is explicit and honest about
this ("reverse-engineered but widely used"), and for a personal, local,
educational terminal that is a defensible trade.

**Rejected for AlgoForge**, on two independent grounds. Terms: AlgoForge's
disclosures and Prop Desk policy surfaces make licensing claims that an
undocumented endpoint cannot support. Integrity: these feeds have no SLA and no
stated revision policy, so a backtest run against them is not reproducible, and
G0 exists to refuse exactly that.

### 4.3 Naive market-open calculation

`TopBar.tsx:marketStateNY()` computes NYSE open from weekday and minute-of-day.
It is wrong on every market holiday and every early close.

**Rejected.** Under §42 a context bar asserting `NYSE OPEN` on Thanksgiving is
fabricated market state. AlgoForge may show session state only where it has a
calendar to back it — and `health.py` already models this correctly for
datasets, classifying full-session closures by size while explicitly declining
to guess *why* the market was shut.

### 4.4 Client-side-only workspace persistence

`zustand/persist` to `localStorage` under one key.

**Rejected.** AlgoForge's workspace store is server-side and versioned, with
`workspace_history` / `restore_workspace_version` / `export_workspace` actions
already registered. Adding `localStorage` persistence would be the second
competing workspace system §3 forbids. Per-viewer conveniences (a collapsed
rail) may live in the browser; the workspace may not.

### 4.5 The equity-terminal widget set

Screener, sector heatmap, options chain, crypto board, insider filings,
portfolio tracker, TradingView embed.

**Rejected**, per §65 (*"If OpenTerminal contains a feature that does not
materially improve AlgoForge: REJECT IT"*). AlgoForge trades CME futures on
validated systematic strategies. A US-equity screener is not a research
instrument here, the data to back it is not licensed, and `PanelKind` is a
closed set precisely so that it cannot fill up with panels nobody uses.

### 4.6 "No API key required" as a design goal

OpenTerminal's headline promise is zero keys and zero subscriptions.

**Rejected as a goal**, adopted as a *floor*. AlgoForge must run locally
without cloud infrastructure (§35), and it already does. But free public
endpoints are indicative data, and an evidence system whose defining feature is
never paying for data has resolved the wrong constraint. The correct position
is the one `Dataset.cost_note` already takes: state what each source costs and
what it is good for, and let the operator choose.

---

## 5. What this leaves to build

Ordered by value, smallest first where the value is equal.

1. **Campaign lifecycle in the action registry** (§1.2). Closes the
   UI/palette/AI asymmetry. Reuses `CampaignService` entirely.
2. **Palette executes actions** (§1.1). Extends the existing component.
3. **Workstation context** — instrument / campaign / strategy / account, with
   resolution-at-render (§2.2), replacing `link_group`'s false promise (§1.3).
4. **Freshness envelope + tiered fallback** (§3.1, §3.2), extending
   `ProviderDescriptor.authority`.
5. **Service health + unified Data Health** (§2.1), composing the existing
   `HealthReport`.
6. **Calendar lifted out of Prop Desk** (§3.3) and made available to research
   (§16).
7. **Scoped assistant context** (§3.4).
8. **Documented keyboard map** (§2.3).

Every item reuses an existing AlgoForge component. No item introduces a second
version of something that exists.

## 6. What is deliberately not in that list

- Headline news (§14). AlgoForge has **no** headline news feed — `news.py`
  models *scheduled economic events*, despite its name. The honest options are
  a provider abstraction with an UNAVAILABLE state, or nothing. Fabricating
  headlines, or scraping them, is excluded by §4 and §42.
- Market regime (§17, §18). `strategy_regimes` and `research/` regime handling
  exist; whether they can support a *live* market-context panel needs its own
  investigation before anything is promised.
- Copy Trader (§30) and autonomous deployment (§33) already exist in
  `propdesk/`. Whether they need anything from OpenTerminal is doubtful —
  OpenTerminal has no execution layer at all.
