# QuantPad — product and analytical UX research

Research conducted against the publicly observable product at <https://quantpad.ai/>:
the marketing surface, the interactive demonstrations it exposes, the published
capability descriptions, and the stylesheet the site serves. No private
endpoint, internal prompt, source file or proprietary component was sought or
examined. Nothing below is copied; what follows is an account of *why* the
observable experience works and what AlgoForge should conclude from it.

The purpose of the exercise, restated so the output can be judged against it:
not "how do we look like QuantPad", but **what makes sophisticated quantitative
analysis feel understandable, and how does AlgoForge build a better version on
its own architecture.**

---

## 1. What the publicly observable product does well

The demonstration surface presents four analytical faces behind one tab strip —
**Prop Firm**, **Verdict**, **Regimes**, **Risk & Monte Carlo** — over a single
uploaded trade log. That is the most instructive structural decision on the
page, and it is worth stating precisely why:

> One body of evidence, four questions asked of it.

Not four features. The same trades, interrogated four ways. A product that
shipped those as four tools would have four datasets, four import steps and
four opportunities for the numbers to disagree. As four views of one ledger,
moving between them is a change of question rather than a change of context.

**The Prop Firm face.** Six headline figures over a fan of a thousand simulated
equity paths, with a scrubber along trading days and a per-day readout beneath
it. The figures observed publicly: net EV (mean), payout probability, mean
payout, days to first payout, and net EV at the 5th and 95th percentiles. The
per-day readout carries median equity, median P&L, a p5–p95 band, and the share
of paths losing.

**The Verdict.** A letter grade A–F across four named axes — edge, robustness,
risk, sample size — plus flagged "tells" of an overfit backtest, described
publicly as things like a suspiciously smooth equity curve or an expectancy
dipping below zero.

**Regimes.** Every trade mapped to the market regime it ran in, by trend and
volatility, with a regime-switching Monte Carlo that resamples returns while
preserving how good and bad conditions cluster.

**Risk & Monte Carlo.** Thousands of resampled equity paths turning one backtest
into a distribution, read for drawdown, risk of ruin and value at risk.

---

## 2. The analytical UX principles underneath

Five, in descending order of how much they should change AlgoForge.

### 2.1 A point estimate never appears without its own tails

Net EV (mean) sits beside net EV at the 5th and 95th percentiles — the *same
quantity*, three ways, adjacent. Not "here is the expectation, and elsewhere is
a distribution chart". The adjacency is the teaching: it makes the spread a
property of the number rather than a separate topic a reader has to go and look
up.

This is cheap to adopt and it is the single highest-value observation in this
research.

### 2.2 The distribution is scrubbable in time

A histogram answers "what does the distribution look like". A fan of paths with
a day scrubber answers "what does it look like **on day 17**", which is the
question a person in an evaluation actually has. Uncertainty that grows with the
horizon is legible as a widening band and illegible as a summary statistic.

### 2.3 State is a toggle over one analysis, not a separate report

Challenge and Funded switch the equity-path chart while the metrics show both
phases. The user is not choosing which report to open; they are asking the same
analysis a different question about the account they hold.

### 2.4 A grade is a decomposition, not a score

A–F across edge, robustness, risk and sample size. The composite is not the
product — the four axes are, because they say *which* thing is weak. A single
number would be a ranking; four named axes are a diagnosis.

### 2.5 The method is named in the copy, not buried

"Regime-switching Monte Carlo", "resamples your returns while preserving how
good and bad conditions cluster". The claim and its method arrive together, so
a reader who knows what block resampling is can calibrate immediately, and one
who does not has been told what to go and learn.

---

## 3. Colour and visual semantics

The site's stylesheet declares its semantic palette as custom properties, and
the *taxonomy* — not the values, which are not adopted — is the finding.

Four observations worth having:

**`gain`/`loss` are separate tokens from `success`/`destructive`, even where the
hue coincides.** P&L sign and validation state are different semantic axes that
happen to share a colour. Keeping them apart means either can be restyled
without the other, and more importantly it forces the question "which of these
two things is this element saying?" at authoring time.

**The categorical chart ramp leads with neutrals.** Series 1–3 are the
foreground colour and two greys; chromatic hues only enter at series 4. A chart
with three series is therefore achromatic, and colour is spent on meaning rather
than on series identity. This is a real discipline and most dashboards fail it.

**The interactive accent is deliberately off the P&L axis** — an indigo/violet
rather than a green. "Selected" can never be misread as "profitable".

**Warning has a chart-specific token** alongside its UI token, so a threshold
drawn on an axis is the same amber as a warning badge.

### What AlgoForge concluded

AlgoForge's token file already enforces a stricter version of all four: green
and red carry P&L and nothing else, the accent is deliberately low-chroma for
exactly this reason, verdict states own a third triad in which `UNKNOWN` is
distinct from `FAIL`, evidence tier is a *ranked ordinal* scale, and the
categorical series are checked for deuteranopia separation. The audit's verdict
on colour semantics was **ALREADY STRONG**, and the honest conclusion is that
AlgoForge should not adopt anything here.

One real gap surfaced, and it was implemented:

**No continuous scale for magnitude.** `--pass` and `--fail` say which side of
zero a value falls on and nothing about how far, so a cell that made a hundred
dollars and one that made forty thousand painted identically. A **diverging,
zero-anchored** ramp of five ordinal steps per side now exists
(`--heat-pos-1..5`, `--heat-neg-1..5`), inheriting the P&L hues rather than
introducing a third green, with distinct encodings for exact zero and for
no-observation. Anchoring at zero rather than at the extremes present is the
part that matters: a minimum-anchored scale paints the least-bad losing cell as
neutral and, on an all-losing strategy, paints one of them green.

---

## 4. Information density

The demonstration puts six metrics, a thousand-path chart, a scrubber and a
four-value per-day readout inside roughly one screen, and it does not read as
cluttered. Three reasons:

1. **The metrics are one family.** Six numbers about one question (will this
   account pay out) read as one object. Six numbers about six questions read as
   six things.
2. **The chart is the largest element and the metrics annotate it.** The
   hierarchy is unambiguous, so the eye has one place to start.
3. **The scrubber converts depth into interaction rather than into area.** Day
   1 through day 39 is thirty-nine screens of information occupying one.

The principle AlgoForge takes: **depth should cost interaction, not area.** More
understanding per screen is achieved by making one surface answer more questions
on demand, not by adding surfaces.

---

## 5. Prop Firm UX

The chain the demonstration makes legible is:

```
ACCOUNT → RULES → STRATEGY → ALLOCATION → RISK → SIMULATION
        → PROBABILITY → OUTCOME → DECISION
```

and the specific thing it does well is turning "will I pass" into a *distribution
with a horizon* rather than a yes/no or a single probability.

AlgoForge's position after audit: **ALREADY STRONG, and in several respects
stronger.** `forge.prop.engine` already produces pass rate with a Wilson
interval, risk of ruin, a boundary race summary, a target-reach curve, a
terminal histogram, a return/drawdown map, tail risk, failure reasons counted
over *every* path (with the sample distinguished from the population), and
equity paths — over a stationary block bootstrap that preserves losing streaks.
`forge.propdesk.survival` sizes against a bootstrapped drawdown quantile chosen
by the operator's appetite, and returns `Unmeasurable` naming the shortfall
rather than falling back to a normal assumption.

Two things AlgoForge already does that the reference does not visibly do, and
which should be protected rather than traded away for polish:

- **Every assumption is named and explained in the interface**, not implied.
  `RESEARCH_ONLY`, `BLOCK_BOOTSTRAP`, `DAILY_SETTLEMENT_APPROXIMATION`,
  `UNVERIFIED_RULES`, evidence tier — each rendered with a sentence saying what
  it commits to, and anything unrecognised shown verbatim rather than dropped.
- **A rule that cannot be checked reports `NOT MEASURED`**, on the same
  three-valued discipline as the judge, rather than being silently treated as
  passed.

The adoptable gap is presentational: the point estimates are not yet uniformly
adjacent to their own tails, and the equity-path fan is not scrubbable by day.
Both are recorded as future work below rather than implemented, because they are
presentation changes to an analysis that is already correct, and this phase had
larger correctness gaps to close first.

---

## 6. Monte Carlo UX

The reference's framing — "size your bets around the tail you can survive, not
the lucky run you happened to get" — is the right sentence, and the regime-aware
resample is the right method for the reason it gives: losses cluster because the
conditions causing them persist, so an IID resample of a regime-dependent
strategy understates its drawdown.

AlgoForge already implements exactly this comparison. `/strategies/{id}/resample`
returns IID **and** regime-aware side by side, and the route's own docstring
states that the gap between them is the point. `forge.propdesk.survival` uses a
stationary block bootstrap with a five-day block precisely so losing streaks
survive resampling.

Audit verdict: **ALREADY STRONG.** Nothing from the reference needed adopting.
The AlgoForge discipline that should be held is the refusal in
`survival.Unmeasurable`: with too short a series there is no tail to estimate,
and the answer is a named shortfall rather than a normal approximation.

---

## 7. Heatmap UX

The reference's regime map — every trade placed by trend and volatility — earns
its place by answering a question that no P&L column can: *where does the money
come from.*

AlgoForge had the analysis and not the visualisation. `forge.analytics.regime`
produced a 2×2 (trend × volatility, plus an honest fifth `UNCLASSIFIED` state)
with per-cell P&L, win rate, average trade, **bar exposure** and **trade share**,
an `insufficient` flag below twenty trades, a transition matrix in counts, and a
concentration measure. The interface rendered it as four cards in a row.

Four cards in a row throw away the axes. "This strategy is fine until volatility
rises, in either direction" is a row that is red when laid out as a grid, and a
fact a reader assembles from four labels when laid out flat.

**Implemented.** `RegimeMatrix` renders the 2×2 as a 2×2, with:

- position fixed, so the grid means the same thing on every strategy;
- shade encoding magnitude on the zero-anchored diverging scale, applied to the
  cell rather than the text so the figure keeps full contrast at every step —
  the failure mode of every heatmap that fills a background and then writes on
  it;
- hatching, not dimming, for a cell whose sample cannot estimate anything —
  dimming is already how this interface says *disabled*, and an unmeasured cell
  is not a disabled one;
- a measure switch (net P&L / per trade / exposure) that rescales without moving
  the axes;
- the trade count on **every** cell, not only the doubtful ones, because a
  reader should not have to notice the absence of a warning to know a number is
  trustworthy.

---

## 8. Strategy storytelling

The target structure, from the directive:

```
FACT → VISUAL → INTERPRETATION → EVIDENCE → IMPLICATION
```

The danger in implementing it is obvious on inspection: a language model asked to
summarise a four-cell regime table produces fluent sentences in **every** case,
including the cases where the table supports nothing — and a sentence that reads
the same whether or not the evidence exists cannot be told apart from one that
was earned.

**Implemented as arithmetic.** `forge.analytics.reading` turns a `RegimeReport`
into findings by comparison and division, never by a prompt. Six questions it can
answer:

| Finding | The question |
| --- | --- |
| `SOURCE` | Where did the money come from, and how concentrated is that? |
| `DRAG` | Where was it lost? |
| `MISMATCH` | Where is time spent without being paid for? |
| `PERSISTENCE` | Does the helpful condition last? |
| `UNTESTED` | Where has this not been tried? |
| `COVERAGE` | What could the classifier not place? |

It emits only the findings the report actually supports. A thin cell yields no
rate claim. A full-sample volatility basis downgrades every finding to
`DESCRIPTIVE` — the arithmetic is identical, the standing is not, because the
labels used information later than the bars they label. Persistence is refused
below two hundred observed transitions, because a 95% built on forty bars is an
order statistic rather than a rate.

`MISMATCH` is the finding worth calling out as beyond the reference: a regime
holding 40% of the clock and 4% of the profit is **positive** in a P&L column and
therefore invisible there. Exposure and contribution are separate facts and the
gap between them needed its own finding.

Every claim carries the numbers it rests on, one click away rather than a page
away — the difference between a claim a reader can check and one they have to
take.

---

## 9. Where AlgoForge can exceed the benchmark

Five, and they follow from architecture the reference does not have:

1. **Provenance on every sentence.** AlgoForge distinguishes a stated belief, a
   model's paraphrase, a rendered action result and a deterministic computation,
   and shows which is which. A conversational analytics product without that
   distinction will eventually quote its own prose back as a finding.
2. **`NOT MEASURED` as a first-class state.** The judge's three-valued
   discipline means "not established" never passes for "passed". Most analytics
   surfaces have two states and silently merge the third into the safer-looking
   one.
3. **Temporal integrity as a property of the classifier, not a promise.** Regime
   labels are built from trailing thresholds and the full-sample alternative is
   available, marked, and demoted to descriptive wherever it is used.
4. **The strategy is an object, not a file.** A canonical IR means an explanation,
   a port, a validation and a backtest all refer to the same thing, and cannot
   drift.
5. **The refusal is the feature.** `Unmeasurable`, `INCOMPLETE`, "start of
   archive", "not an estimate", "no panel yet" — each is a place the system
   declines to look more capable than it is.

---

## 10. What AlgoForge should deliberately not adopt

- **A composite letter grade.** A–F across four axes is a good decomposition and
  a bad summary. AlgoForge has a fourteen-gate ladder whose whole value is that
  it says *which* gate failed; collapsing that to a letter would discard the
  diagnosis and invite exactly the "B+ means good enough" reading the ladder
  exists to prevent. The four axes as a *display* of the ladder are worth
  considering; the letter is not.
- **"Overfit tells" as a heuristic flag.** A suspiciously smooth equity curve is
  a reasonable prompt for a human and a poor basis for a verdict. AlgoForge has
  CSCV, CPCV and walk-forward, which measure the thing the tell gestures at.
  Adding a heuristic beside a measurement invites the heuristic to be read as
  one.
- **Import-a-trade-log as the primary entry.** It is the right front door for a
  product whose users have logs elsewhere. AlgoForge's strategies are canonical
  objects that produce their own ledgers; an import path as the main entrance
  would make the derived artifact the subject.
- **Social / cloning surfaces.** Out of scope for a local, private, paper-only
  workstation, and they would bring a provenance problem that this system's
  entire discipline exists to avoid.

---

## 11. Implementation priorities, and what was done

Ranked by impact × user value × analytical validity ÷ cost.

| # | Finding | Classification | Outcome |
| --- | --- | --- | --- |
| 1 | Regime grid rendered as a grid, with magnitude | NEEDS VISUALISATION | **Done** — `RegimeMatrix` |
| 2 | Deterministic FACT→IMPLICATION reading | NEEDS ANALYTICAL BACKEND | **Done** — `forge.analytics.reading` |
| 3 | Diverging zero-anchored intensity scale | NEEDS DATA SUPPORT | **Done** — `--heat-*` tokens |
| 4 | Colour semantics | ALREADY STRONG | No change |
| 5 | Monte Carlo method and honesty | ALREADY STRONG | No change |
| 6 | Prop Firm analysis depth | ALREADY STRONG | No change |
| 7 | Point estimates adjacent to their own tails | NEEDS UX IMPROVEMENT | Future work |
| 8 | Scrubbable equity-path fan by trading day | NEEDS UX IMPROVEMENT | Future work |
| 9 | Challenge/Funded as a toggle over one analysis | PARTIALLY IMPLEMENTED | Future work |
| 10 | Composite letter grade | NOT APPROPRIATE | Rejected, with reasons above |
| 11 | Overfit "tells" heuristic | NOT APPROPRIATE | Rejected, with reasons above |
| 12 | Trade-log import as primary entry | NOT APPROPRIATE | Rejected |
| 13 | Social / cloning | NOT APPROPRIATE | Rejected |

Items 7–9 are presentation changes to analyses that are already correct. They
were deferred behind the correctness work in this phase — a chart that cannot
pan through history and a regime grid that discards its axes were larger
problems than a metric that is one click from its percentiles.

---

## The finding this research turns on

The reference's strength is not any individual analysis. It is that **one body
of evidence answers four questions without the user changing context**, and that
**every number arrives with the method that produced it named beside it.**

AlgoForge already has deeper analytics than it was showing. The work this
research justified was almost entirely presentational — and the presentational
work was worth doing precisely because the analysis underneath was already
sound. The opposite order would have produced a convincing surface over
arithmetic nobody had checked, which is the failure this whole codebase is
organised against.

> **More understanding per screen**, and never more confidence than the evidence
> supports.
