# Command Palette Architecture

One search field over every record the workstation holds, and every verb it can
run.

Source: `apps/web/src/components/CommandPalette.tsx`, `apps/web/src/workstation.ts`,
`apps/api/forge_api/actions.py`.

---

## What was already there

A palette, 166 lines, bound to `Ctrl/Cmd+K` in `App.tsx`, searching six record
types — views, strategies, experiments, runs, constraints, datasets — grouped
rather than ranked into one list, with the keyboard cursor kept on screen.

It was **navigation-only**. Every result called `onRoute(route)`, which set a
hash. There was no path from the palette to an action, so "start the NQ
campaign" meant finding the campaign, opening its screen, and clicking a button.

So this was not "build a command palette". It was "give the existing palette
the action registry" — a smaller and safer change than the brief's §4 implies.

## The rule

**Every command is a registered action.** A palette row that runs something
posts to `POST /api/v1/actions/<name>` — the same registry the assistant calls,
the same one `forge.modes.permissions.evaluate()` is applied to, the same one
the MCP server exposes. There is no palette-only endpoint.

The consequence worth stating: a verb the registry does not have cannot be
offered by the palette either. That is why campaign lifecycle had to enter the
registry (it was HTTP-only) before any of this was possible.

## Groups

| Group | Rows | Enter does |
|---|---|---|
| Commands | Campaign lifecycle, by status | runs the action |
| Instruments | The instrument catalogue | `set_context {instrument}` |
| Views | The mode's sections | navigates |
| Campaigns | Every campaign | navigates |
| Strategies, Experiments, Runs, Constraints, Datasets | records | navigates |

Each row ends in **what it does** — `open`, `start`, `pause`, `stop`,
`set context` — rather than in a bare `↵`. "Open" and "stop" are very different
things to be one keystroke away from, and a key is not an effect.

### Lifecycle by status

A campaign offers only the transitions it actually has:

```
running        -> Pause, Stop
paused         -> Resume
created/stopped-> Start
archived       -> nothing (it must be restored first)
```

Offering "Start" on a running campaign would be a control that refuses, drawn
as one that works.

## What is deliberately not offered

**Anything that needs the operator's confirmation.**

`Actions.call` refuses any action above `ActionRisk.SAFE` unless `confirmed` is
passed, and `confirmed` is the operator's answer rather than the caller's
opinion. The HTTP endpoint's request model carries `arguments` and nothing else,
so it cannot pass it.

That boundary was **not widened** for the palette. Widening it would mean any
HTTP caller could set `confirmed: true`, which is precisely the escape route the
flag exists to close. So `archive_campaign` (CONFIRM) is not a palette command;
it is done on the screen that can ask.

The palette therefore offers only verbs that will actually run. A refusal is
still possible — a mode may deny an action — and when one happens the message
is shown **verbatim, with the palette left open**, because the registry's
refusals name what was wrong and what would fix it, and a toast that vanishes
throws that away.

## Ordering

Groups are ordered by a fixed list while nothing is typed, and **by their best
match once something is**.

That second rule came out of QA on the running application. With Commands
pinned first, typing `NQ` put *"Start campaign · NQ Momentum"* at the top —
the campaign's name contains NQ — so Enter would have started a research
campaign when the reader meant to look at an instrument. Within a group, an
exact label match sorts above a prefix match, which sorts above a mere
substring: somebody typing `NQ` means NQ, not MNQ.

Both are pinned by tests, because both were found by looking at the real thing
rather than by reasoning about it.

## Cost

Record queries run only while the palette is open (`enabled: open`), so the
shortcut costs nothing when it is closed. The action schema list and the
instrument catalogue are fetched with `staleTime: Infinity` — the registry is
fixed for the life of the process, and re-fetching it on every open was a
request for a list that cannot change.

After a command runs, queries are invalidated broadly rather than by name. The
palette can run any of several verbs and does not know what each one moved, and
a campaign row still reading "running" after it was stopped is worse than a
refetch.

## Accessibility

- The dialog is `role="dialog" aria-modal="true"` with a name.
- `Escape` closes; `↑`/`↓` move; `Enter` acts.
- The cursor is a highlight rather than focus, so an effect scrolls it into
  view — guarded, because jsdom has no layout and throwing out of that effect
  would take the palette down in tests.
- Rows are `<button>`s, so the palette is fully operable without a mouse, and
  the verb at the row's end is part of the accessible name.
