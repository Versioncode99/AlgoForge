# AlgoForge interface — direction

Written before the CSS, per the vault's web method. Every line here should be
untrue of a generic dashboard.

---

## The world

**A research control room for a machine whose job is to say no.**

AlgoForge is not a trading dashboard and must not read as one. A trading
dashboard's job is to make you feel informed and act. This workstation's job is to
reject thousands of ideas credibly, so the interface has to make a *refusal*
legible and worth reading — the reason, the evidence behind it, and what would
change it. The nearest honest relatives are a Strategy Analyzer results window
and a lab instrument's front panel: hairline rules, tabular numerals, controls
that state their units, nothing decorative competing with a number.

## Anti-reference

- **The build this replaces.** Five of eleven tabs were driven by one seeded
  fixture run, so they showed the same numbers whatever you selected. A page you
  cannot act on is worse than a missing page, because it costs trust.
- **The SaaS dashboard.** Rounded cards in a 3-column grid on slate, one KPI per
  card, generous whitespace. Wrong category: this is a dense-data tool and
  spacing it out hides the comparison that is the whole point.
- **Threepio's marketing surface.** Green screenshots and a licence counter.
  Where this app shows a number it shows the interval around it.

## Type

| Role | Face | Reason |
|---|---|---|
| Numbers, ids, code, table cells | **IBM Plex Mono** | Drawn for terminals and data. Tabular by default, so columns align and a changing digit does not shift its neighbours — which matters when a progress readout updates three times a second. |
| Interface text, labels, prose | **IBM Plex Sans** | Same superfamily, so the pairing is structural rather than a mood choice. Replaces Manrope, which is one of the faces every generated interface reaches for. |

No display face. There is no hero here; the largest type on screen is a metric.

## Colour

Near-black warm graphite surfaces remain. Restrained copper marks selection,
focus and live research context; green is reserved for semantic success. This
separation stops brand colour from being mistaken for a validation verdict.

- **Three verdict states, three colours.** `PASS` green, `FAIL` red, and
  `INCONCLUSIVE` amber. The judge's entire design rests on "not measured" being
  different from "failed", and the previous build painted both red. That single
  confusion made the gate ladder unreadable.
- **Evidence tier is a colour, not a word.** Holdout, out-of-sample,
  in-sample and synthetic are ranked, and rank should be visible before reading.
- **No gradients.** Flat fields with real edges. A glow behind a panel would be
  a smudge over a table.

## The authored moment

**The job bar.** When a backtest is running, a hairline sweep crosses the top of
the workspace carrying bars processed, rate, elapsed and ETA, and the strategy
row it belongs to pulses once when it lands.

One moment, and it is honest: it is showing real work on real bars, and a
sixteen-year run takes minutes. Everything else resolves quietly — rows stagger
in over 200ms, numbers roll rather than snap, panels expand from their own edge.
No fade-up on everything.

## Structural rules

1. **Research hierarchy is always visible.** Grouped navigation keeps mission,
   experiment, run, validation, evidence and deployment context distinct.
2. **Tables are the primary container, not cards.** Cards are for a single
   object with a picture. This app compares things.
3. **Every section shaped by its job.** The control bar is a strip. Results are
   tabs over one dense table. The prop matrix is a grid. The strategy list is a
   rail. None of them share a rhythm.
4. **No metric without its uncertainty** where one exists. A pass rate ships
   with its interval; a Sharpe ships with its deflated probability.
5. **Every empty state names the next action.** "No backtest yet" is useless;
   "Run a backtest over 3 years — this strategy needs 30 trading days" is not.
6. **Nothing on screen that the user cannot act on.** The test that killed five
   tabs. Apply it to every new panel.
7. **Agents are operations, not theatre.** Show task, state, duration, evidence
   and controls; never simulate thinking with orbital or particle animation.
8. **URLs preserve workspace context.** Every primary view has a stable hash
   route and can be reached from the keyboard palette.
9. `prefers-reduced-motion` removes movement, never information.

## Density target

Modelled on the Strategy Analyzer results window: a control strip, then tabbed
results over one table with **All / Long / Short** decomposition, ~40 statistics
visible without scrolling on a 1440px screen. The previous build showed four
numbers in the same space.
