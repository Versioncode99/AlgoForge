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
| 1 — Architecture | not started | |
| 2 — Workstation UX | not started | |
| 3 — Data / market | not started | |
| 4 — Research | not started | |
| 5 — Prop Desk | not started | |
| 6 — AI | not started | |
| 7 — Visual / performance QA | not started | |
| 8 — Verification | not started | |
| 9 — Documentation | not started | |

## Baselines measured before any change

- Frontend: **86 tests pass**, 11 files (`npx vitest run`, 11.6 s).
- Python: recorded below once the full run completes.
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
