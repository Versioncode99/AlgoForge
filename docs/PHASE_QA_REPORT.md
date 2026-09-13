# Phase QA report

Four directives worked in dependency order. This is the evidence, including
what was not done and what could not be verified here.

Branch: `claude/zen-hawking-nm63gx` · PR: Versioncode99/AlgoForge#10

---

## A. Three-document synthesis

**Document 1 — engineering/research foundation.** Contributed the chart engine
requirement (§26–33), the testing and Git discipline, the deterministic-authority
rule (§51: use deterministic code wherever an LLM adds no value), and the
"continue through failures" posture. Most of its Phase C/D content — model
routing, construction grammar, primitives, mechanisms, external research — is
the subject of the open PR #9 and was deliberately **not** duplicated here.

**Document 2 — product/workstation direction.** Contributed the removal of the
Hedge Fund product mode, the workspace-as-environment framing, and the
multi-window requirement. Superseded Document 1 wherever the two touched
product hierarchy, as instructed.

**Document 3 — analytical UX benchmark.** Contributed the regime grid, the
deterministic reading, and the diverging colour scale. Its audit against the
repository found more **already strong** than missing — colour semantics, Monte
Carlo method, prop-firm depth — and those produced no change, which is the
correct outcome of a benchmark rather than a failure of one.

**Document 4 — AI Chat.** Gated behind the other three, as it states. Its own
audit found the tool loop and the scheduler already present, which changed the
plan: the chat wraps the existing loop rather than building a second one.

**Conflicts, and how they were resolved.**

- *Document 1 wants Pine described, not generated; Document 4 wants porting.*
  Resolved by generating **and** classifying every element, with the claim
  capped at what was actually checked. Document 1's principle — never imply
  more than can be supported — is preserved; the refusal to emit is not.
- *Document 2 removes a mode that owns the only AI path to the book.* Resolved
  by moving the stance rather than dropping or widening the grant, with four
  tests pinning both ends.
- *Repository evidence changed the plan twice.* The assistant's tool loop
  already existed and was good, so no second loop. A scheduler already existed,
  so no research queue.

---

## B. Test results

| Suite | Result |
| --- | --- |
| Backend (`pytest`) | **2838 passed**, 0 failed |
| Frontend (`vitest`) | **248 passed** in 24 files, 0 failed |
| `ruff check .` | clean |
| `mypy` (strict) | clean, 195 source files |
| `tsc --noEmit` | clean |
| `vite build` | clean |
| CI — ubuntu 3.13 | **success** |
| CI — **windows** 3.13 | **success** |
| CI — web | **success** |
| E2E (Playwright, real browser) | 43 passed, 1 skipped, **8 failed — all environmental** |

Backend was 2719 at the start of this branch; 2838 now.

New coverage, by area:

| Area | Tests |
| --- | --- |
| Chart HTTP contract | 15 |
| Chart merge arithmetic | 25 |
| Chart component wiring | 10 |
| Regime reading | 17 |
| Regime grid + reading panel | 20 |
| Conversation store | 30 |
| Conversation HTTP | 26 |
| Chat panel | 17 |
| Strategy porting | 32 |
| Port panel | 9 |
| Desktop window registry | 25 |
| Desktop IPC contract | 11 |
| Desktop bridge (renderer) | 10 |
| Permission boundary (new invariants) | 4 |

### The eight E2E failures

All eight share one cause: this container has no market-data archives and no
`DATABENTO_API_KEY`, so `/bars` correctly refuses with
`market_data_unavailable`, and the chart renders its unavailable state instead
of the provenance footer those tests look for.

Attributed rather than assumed: `git diff main...HEAD` touches neither the
provenance footer in `PriceChart.tsx` nor the refusal path in `market.py`. CI
does not run Playwright, so these were not previously being run either.

Affected: 4 in `charts.spec.ts`, 1 in `workspace.spec.ts` (chart panel), 3 in
`workstation.spec.ts` (a backtest needing bars, a prop matrix needing
sufficient days, a screenshot capture needing both).

---

## C. What changed

| Area | Change |
| --- | --- |
| Chart | Symmetric exclusive paging, coverage bounds, browser-side merge/dedupe/order, viewport preserved across a prepend, start-of-archive stated |
| Analytics | `forge.analytics.reading` — six deterministic findings with evidence and standing |
| Visualisation | `RegimeMatrix` 2×2 on a zero-anchored diverging scale; `RegimeReading` panel |
| Design tokens | `--heat-pos-1..5` / `--heat-neg-1..5` / zero / empty / thin, in all three themes |
| Product | Hedge Fund mode removed; book loop → Normal; oversight → AI; stance → AI |
| Chat | `forge.conversation` store, `forge_api.chat` service, 9 HTTP routes, the `AGENT` panel filled |
| Porting | `forge.strategy.porting` — element classification, status ceiling, Pine emitter |
| Desktop | Window registry, IPC contract, preload bridge, debounced session persistence |

---

## D. What deliberately did not change

- **Colour semantics.** Already stricter than the benchmark. No change.
- **Monte Carlo.** IID and regime-aware already served side by side, with a
  block bootstrap that preserves losing streaks and an `Unmeasurable` refusal
  rather than a normal fallback. No change.
- **Prop Desk analysis.** Wilson intervals, boundary race, target-reach curve,
  tail risk, failure reasons over the full population, every assumption named.
  No change.
- **The permission policy's shape.** One grant moved with its stance; nothing
  else.
- **PR #9's files.** Untouched, so the two merge independently.
- **A second agent loop, a second scheduler, a knowledge graph, arbitrary code
  execution, NinjaScript/MQL5 generation.** Reasons in
  `docs/AI_CHAT_ARCHITECTURE.md`.

---

## E. Security

No new action surface. The chat calls the same `forge_api.actions` registry as
`Actor.AI`, through the same `forge.modes.permissions` pure function of actor,
mode, stance and action facts — none of which a model can set.

| Property | Assertion |
| --- | --- |
| Exactly one configuration reaches the book | `test_exactly_one_configuration_reaches_the_book` |
| It requires an explicit stance, and entering the mode does not enter it | same |
| Every other configuration holds the book for a person | `test_the_book_grant_did_not_move_somewhere_easier_to_reach` |
| Automation grants did not spread | `test_automation_did_not_spread_when_a_mode_was_removed` |
| The full policy surface, unchanged row for row | `test_no_configuration_gained_anything_it_did_not_have` |
| Asking politely for a protected control changes nothing | `test_asking_for_a_protected_control_does_not_produce_one` |
| One conversation cannot reach another's context | `test_a_conversation_cannot_reach_another_conversations_context` |
| IPC refuses unknown channels and undeclared fields | `ipc-contract.test.js` |
| Every IPC channel is a window-management verb | same |

The desktop shell keeps `contextIsolation: true`, `sandbox: true`,
`nodeIntegration: false`. The preload exposes named functions, not a generic
`invoke`, so the channel is never a value the page chooses.

---

## F. Bugs found, and how

Four found by reading output or driving the real interface rather than by a
test going red:

1. **`ta.atr(14.0)`** — Pine lengths are `simple int`. The script looked right
   in a diff and did not compile.
2. **`ta.dmi(n,n).adx`** — returns a tuple, has no member access. The mistake a
   translator makes reasoning about a function from its name.
3. **A stop on a shifted feature** read the current bar where the signal read an
   earlier one. Silently a different stop distance.
4. **The first message of every new conversation** was posted to
   `/conversations/null/messages` and lost — a stale closure over `activeId`.
   Invisible to a unit test whose mock accepted any path ending in `/messages`.
   Caught by the E2E suite; the unit test now asserts the path.

One more found while writing a test: `scrollIntoView` called unguarded is
optional in the DOM, so any host without it crashed the chat panel on mount.

---

## G. Not done, and named

| Item | State |
| --- | --- |
| Docking geometry and workspace-group UI | Registry, contract, preload and lifecycle are in and tested; the visual composition is not built |
| Point estimates adjacent to their own tails | Deferred — presentation change to a correct analysis |
| Scrubbable equity-path fan by trading day | Deferred, same reason |
| Challenge/Funded as a toggle over one analysis | Partially implemented; deferred |
| Opening-context auto-attachment from chart and strategy surfaces | The panel accepts an `opening` context; call sites do not pass one |
| Artifact deep links for `port`, `resample`, `parameter_surface` | Reported honestly as "no panel yet" rather than linking nowhere |
| Research-quality benchmark (construction diversity, SAME_CONSTRUCTION rate) | Belongs to PR #9's vocabulary work, which is not on this branch; running it here would measure the wrong tree |
| Multi-window soak under a real Electron process | The registry has a 50-round open/close test; driving a real Electron app headlessly was not attempted |

---

## H. The product test

> Are we making AlgoForge genuinely better, or merely bigger?

Four of the six things built here **removed** something: a mode that was a
product category, a chart that was a photograph, a grid that discarded its
axes, a console that lost your research. The two additions — porting and the
conversation store — are both built around a refusal: one caps what a port may
claim, the other refuses to let a sentence acquire standing it did not earn.

The repository is larger by roughly 120 tests and four modules, and smaller by
one product mode, one parallel console implementation, and one class of silent
data loss.
