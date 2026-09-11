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
