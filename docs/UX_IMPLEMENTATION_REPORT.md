# UX Implementation Report

What changed on screen, what it replaced, and what was checked.

---

## 1. The three lies the interface used to tell

**"Engine: RUNNING."** Read from a boolean that meant "worker threads exist". A
search that had exhausted its campaign, condemned every template it could reach,
or was refusing every proposal said RUNNING for as long as it was left alone.

**"Skipped by memory: 1,290."** One number summing five unrelated situations —
an exhausted campaign, a builder that raised, an empty frontier, a novelty
collision and a true duplicate — and reported as compute saved.

**"Validation: 0."** Indistinguishable between "nothing was eligible" and
"everything was tried and everything failed", which are opposite facts.

All three are gone. The engine renders a derived state with a reason and a
remedy; refusals render as a breakdown with a novelty hierarchy under it; and
validation renders as attempts / passed / failed / inconclusive / blocked /
queued.

## 2. Making complexity legible, not removing it

Nothing was simplified away. The Research Control Center shows *more* than the
screen it replaces — every campaign, every agent's heartbeat, every refusal's
matched object — and is readable because the hierarchy is explicit rather than
because the content is thin.

The design decisions that did the work:

- **Colour means something specific.** Green is reserved for RUNNING and for
  compute genuinely saved. An **exhausted campaign is neutral, not red**: it
  reached its target, and colouring that as a fault teaches operators that
  finishing is failing. A *stalled* campaign is amber, because that one is a
  problem.
- **Only a working engine animates.** The badge's pulse is a claim about
  activity and an idle engine must not make it.
- **Editing is behind a toggle, not on hover.** A rail row that sprouts five
  icons when the pointer crosses it is a row you have to aim at, and the rail is
  mostly used to get somewhere.
- **Labels do not overclaim.** "Already answered" rather than "Duplicates
  prevented", because most refusals in that bucket are restatements rather than
  byte-identical repeats — calling 914 of them duplicates when 19 are exact is
  the same overclaim the old counter made.

## 3. Progressive disclosure

The existing `GUIDED / ADVANCED / QUANT` model is untouched and the new surfaces
respect it: the Control Center's headline metrics read at a glance, the novelty
breakdown and the worker heartbeat table sit below them, and the refusal ledger
only appears once a campaign is selected.

Every metric carries a one-sentence explanation on its `title`, written to be
read by somebody who does not already know the answer — "Distinct economic
explanations, not parameter sets" rather than "mechanisms".

## 4. The end of mode switching

The workspace switcher replaces the mode chooser as the way to move between
arrangements. "Create workspace" is the most visible control after the list
itself, because the change is worth nothing if nobody finds it.

Creating one composes its rail in the same call. A workspace built in this
session, through the same action registry the assistant uses:

```
My Prop Research  (icon NQ)
  Desk        Prop Desk · Copy Trader · News
  Risk        Risk
  Autonomous  Research Control* · Research Campaign      (* pinned)
  AI          Agents
  Strategy    Validation
  MY TRADING  NQ Chart
```

Destinations from four different modes, one arrangement, no switching. Changing
*mode* is still possible and now lives inside the switcher, labelled for what it
is: "Decides what an assistant may do on your behalf. Your workspaces are
unaffected."

The context bar names the workspace when a custom rail is in use, with the mode
beside it in small caps — because the mode still governs permissions and hiding
that would be its own dishonesty.

## 5. Failure states

Every stalled state carries three things: what is happening, why, and what to
try. Example, rendered live during QA:

> **Exhausted** — 41 consecutive proposals were duplicates; the frontier around
> this objective is exhausted.
> **What to try:** broaden the objective, allow another mechanism domain, enable
> literature retrieval, or widen the parameter ranges.

`FAILED`, `NOT MEASURED` (inconclusive), `BLOCKED` and `EXHAUSTED` are rendered
distinctly everywhere they appear and are never collapsed into "failed".

## 6. Visual QA — what was actually checked

Driven with Playwright against the real API and a real running engine with two
campaigns, five agents and 42 campaigns under load.

| Width | Checked | Result |
| --- | --- | --- |
| 1920 | Control Center, campaign open, switcher, create, pipeline | clean |
| 1440 | as above, plus the custom rail and its picker | clean |
| 1280 | as above | clean |
| 420 | as above, via the overlay rail | clean |

Assertions made at every width: **no console errors, no page errors, and
`document.scrollWidth <= window.innerWidth`** — the page body never scrolls
sideways. Tables and the destination picker scroll inside their own containers.

**Long content:** campaign names of 76 characters wrap; descriptions clamp to
three lines; agent tasks and skip reasons truncate to 26ch with the full text on
`title`. Checked with deliberately long generated names.

### Three real bugs the visual QA found

1. **The destination picker rendered back to front.** `.ws-picker-body button em`
   auto-placed into column 1 and pushed the label into column 2, so a disabled
   row read "ALREADY HERE … Prop Desk". Both now have explicit grid columns.
2. **The search icon stacked above its input.** `.ws-picker label` (class + type,
   specificity 0-1-1) was beating `.ws-picker-search` (0-1-0) and forcing the row
   to `grid`. Fixed by raising the search rule's specificity.
3. **The switcher closed itself.** It was dismissed by an effect on `route`,
   which also changes when the shell corrects a hash in the background — so the
   panel could vanish because something loaded. It now closes on *navigation*.

A fourth was found by the shell's own unit test rather than by eye: moving
"Switch" from leave-mode to open-switcher broke the test that asserted a way back
to the chooser existed, which is exactly what that test is for.

## 7. Accessibility

- Every control is a real `<button>`, `<a>` or `<input>` with an accessible name;
  icon-only controls carry `aria-label`.
- Group collapse uses `aria-expanded`; pin and hide use `aria-pressed`; the open
  campaign card uses `aria-expanded`.
- The current destination carries `aria-current="page"`.
- Focus is visible: `:focus-visible` outlines on rail items, inherited elsewhere.
- The rail scrim and the switcher scrim are buttons with names, so they are
  reachable and dismissible from the keyboard.
- Colour is never the only signal: stale workers carry the word "stale", pinned
  items carry a pin icon *and* `aria-pressed`, and campaign status is a word.
- Tables use `<th scope>`; the agent-remove column header is visually hidden but
  present for screen readers.

## 8. Performance

Measured with 42 campaigns and five agents against the live API:

| Measure | Result |
| --- | --- |
| `/campaigns/control-center` | 11 ms, 64 KB |
| 42 campaign cards rendered | 882 ms from navigation |
| Click handled while rendering | 207 ms |
| Horizontal overflow | none |
| Console errors | none |

The Control Center polls every 5 s and the payload is one request rather than
six, so the numbers on screen are consistent with each other rather than five
refreshed and one stale.

**One real inefficiency this found and fixed:** with both campaigns exhausted the
engine had run 768 cycles of which 749 were barren — spinning at its configured
interval against research that could not proceed, costing a core and burying the
one event that mattered under thousands of identical ones. `RuntimeMonitor.backoff`
now grows the wait towards 30 s as barren cycles accumulate and returns to full
rate the instant anything progresses. The reported state is unchanged; only how
often the same answer is recomputed.

---

# Part two — the OpenTerminal integration

What changed on screen in the workstation phase, what it replaced, and what was
checked on the running application rather than in a test.

## 1. The lie this phase found

**The link badge.** `Panel.link_group` was declared with the docstring *"Panels
sharing a link group follow each other's symbol and timeframe"*. The
`link_panels` action's summary repeated it. `Workspace.tsx` drew the group's
name as a badge on the panel header. And nothing anywhere made any panel follow
any other: `Workspace.linked()` wrote the tag and no code read it.

So the interface showed the operator a badge asserting a behaviour that did not
exist. By §61's definition that is a fake feature, and it was one AlgoForge had
already shipped.

It is now true. A panel in a link group displays the group's context; a panel
outside one displays its own settings; and **nothing is ever written to a
panel**, so unlinking reveals the symbol it was always pinned to. The badge's
tooltip says which of the two is happening.

## 2. What is on screen that was not

**The subject.** The context bar could name the workspace and the mode and could
not name the instrument, the campaign, the strategy or the account. It now
renders one chip per set facet, each with its own label and its own clear
button, in a group visually separate from the telemetry beside it and the mode
badge before it — because those are three different kinds of fact and the
previous phase's bug was exactly that kind of conflation.

An unset context renders **nothing**. Six chips reading "—" is a row of controls
that look broken, and a context nobody has set is not a fault.

**Commands in the palette.** The palette searched six record types and could not
run anything; every result set a hash. It now has Commands and Instruments
groups, each row ends in what it does (`open`, `start`, `pause`, `stop`,
`set context`) rather than in a bare `↵`, and a refusal is shown verbatim with
the palette left open.

**Sources, beside archives.** `HealthMatrix` measured datasets on disk very
well. Nothing showed whether a *source* answered. `ServiceHealth` sits beneath
it: each row collapses to a status line and opens to WHAT / WHY / IMPACT /
REMEDY, a source nobody has called reads `NOT OBSERVED` rather than healthy, a
missing credential reads `NOT CONFIGURED` rather than failing, and the three
capabilities this build does not have are **named** rather than omitted.

## 3. Found by looking at the running application

Four faults, none of which a test would have suggested:

**Typing a ticker offered to start a campaign.** With Commands pinned first in
the palette, typing `NQ` put *"Start campaign · NQ Momentum"* at the top,
because the campaign's name contains NQ. On a keyboard-first surface that is a
research campaign one Enter away from somebody who meant to look at a chart.
Group order is now fixed only while nothing is typed.

**MNQ sorted above NQ.** A plain substring filter has no opinion, and MNQ comes
first in the catalogue. Exact matches now sort above prefixes, prefixes above
substrings.

**A header disagreed with its own body.** A panel pinned to MNQ and linked to a
context on NQ drew "MNQ 5m" over a body reading "no archive for NQ". The title
now follows what is shown.

**A resumed campaign un-resumed itself.** Caught by a test that passed alone and
failed in the full suite, then reproduced deliberately: a research worker
holding a campaign row read before a pause wrote `status='paused'` back over the
operator's resume. See the final report.

## 4. Visual QA

Four widths on the running application — 1920, 1440, 1024, 420 — across the
shell, the palette, the palette with a query, and Data Health.

- **No horizontal overflow at any width**, on any of the four screens.
- **No console errors and no page errors** at any width.
- The palette opens, searches and runs at 420 px.
- At 420 the instrument chip drops its timeframe suffix and Data Health's
  four-column status line stacks rather than squeezing.

The only network errors observed were `409 Conflict` from `/bars` for a paid
dataset that has not been downloaded — the API correctly refusing to serve what
it does not have, with the panel showing *"No archive for NQ. Add one, or pick a
different symbol above."* That is the honest empty state §41 asks for, and it is
pre-existing behaviour.

## 5. Accessibility

- The palette dialog is `role="dialog" aria-modal="true"` with a name; rows are
  buttons; `Escape` closes; arrows move; the verb is part of each row's
  accessible name.
- Context chips carry a visually-hidden facet label, so a screen reader hears
  "Instrument: NQ" rather than "NQ".
- Each clear button has an explicit `aria-label` naming its facet.
- Source health rows are `<details>`/`<summary>`, so the disclosure is native
  and keyboard-operable, and the terms are a real `<dl>`.
- The status badge for an unobserved source has its own token rather than
  borrowing the "passed" colour.
- No literal radii: `test_the_shell_names_no_literal_radius` caught one in this
  work and it was changed to `var(--r-sm)`.

## 6. Visual identity

No new card style, no gradient, no metric tile. The chips reuse the existing
`.context-facts` geometry with the brand tint; the source rows reuse the table
and badge vocabulary already in the shell; the palette is unchanged apart from
one outcome line and a right-hand verb column. Progressive disclosure carries
the new density: a source row is one line until it is opened.
