# AlgoForge — final evolution report, 2026-09-08

**Commit:** `6ae1695e999e16646d2b1400e2d9bd00dfa6788a`
**Branch:** `main`, clean, pushed. `HEAD == origin/main`
**Started from:** `3e35fb1` (the previous session's HEAD, plus one uncommitted
`explain.py` that had been committed but never wired to anything)
**Machine:** the laptop, `C:\Users\Videe\Desktop\AlgoForge`

The brief asked for this file and set the standard it has to meet:

> REAL DOMAIN MODEL + REAL DATA + REAL BACKEND + REAL UI + REAL TEST + REAL
> PROVENANCE.
>
> Do not claim something is implemented because a component renders.

Every claim below says which of those it has, and where a number came from.

---

## 1. Measured state

| | start of session | now |
|---|---|---|
| Python tests | 864 | **1,089 passed** |
| Vitest | 12 | **32 passed** |
| Playwright | not run for two sessions | **35 passed** |
| `ruff check` | clean | clean |
| `mypy --strict` | clean, 110 files | clean, **113 files** |
| `tsc --noEmit` | *no script existed* | clean |

```bash
uv run python -m pytest -q                 # 1,089 in ~7m
npm --prefix apps/web run typecheck        # tsc --noEmit
npm --prefix apps/web run test             # vitest, 32
```

Playwright needs both servers up and both environment variables set, or seven
workspace tests fail on a refused connection to the wrong port:

```bash
ALGOFORGE_HOME=<temp dir> uv run python -m uvicorn forge_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8790
cd apps/web && VITE_API_URL="http://127.0.0.1:8790/api/v1" npx vite --host 127.0.0.1 --port 5190 --strictPort
cd apps/web && ALGOFORGE_WEB_URL="http://127.0.0.1:5190" ALGOFORGE_API_URL="http://127.0.0.1:8790/api/v1" npx playwright test
```

**Still outstanding from the last session and not addressed here:** the two
`OPENCODE_API_KEY` values were pasted into a chat transcript. They are
gitignored and were not committed, but they are compromised. **Rotate them.**

---

## 2. What was built

### 2.1 A verdict a person can act on — `packages/forge/judge/explain.py`

Model ✓ · Backend ✓ · UI ✓ · Tests ✓ (38 + 7) · Provenance ✓

`G5 INCONCLUSIVE DSR_NOT_MEASURABLE` is correct and nearly useless to somebody
deciding whether to keep working on a strategy. `explain` reads a finished
verdict and returns the same information ranked: what fired, how much it should
change what you do next, why it matters, and what would address it.

It is a pure function of a verdict that has already been reached — the only safe
place for a layer like this. Decision, grade and every gate status are copied
and cannot be moved. It adds no second grade, because two grades from one body
of evidence is an invitation to quote the kinder one. INCONCLUSIVE ranks below
every real shortfall and above nothing at all.

The suggestions are the part that could do damage. The obvious "fix" for a
deflated-Sharpe shortfall is to declare fewer trials. Every suggestion is about
improving the strategy or gathering more evidence, checked twice — once against
the tables and once against what actually reaches a user over HTTP.

The module existed at the start of the session, committed and imported by
nothing. It is now in the dossier, on the Evidence screen, and behind
`GET /strategies/{id}/findings`.

### 2.2 Prop simulation over the strategy's own trades

Model ✓ · Data ✓ · Backend ✓ · UI ✓ · Tests ✓ (18 + 8) · Provenance ✓

The engine already produced equity paths, a days-to-pass curve, a terminal
distribution, a return-against-drawdown map and a tail summary, all over daily
P&L derived from a strategy's actual fills. `PropResult` described that payload
in the frontend types. `EquityChart`, `TargetReachChart`, `TerminalHistogram`
and `ReturnDrawdownChart` were written to draw exactly those four fields.

None of it was connected, and no test had ever reached a successful simulation —
both existing tests for the route assert a refusal. Nothing failed because
nothing ran.

A matrix cell now opens the full distribution: pass, fail and timeout shares,
expected payout, expected terminal P&L with p05/p95, days to pass and days to
fail with their own percentiles, risk of ruin, CVaR, the account equity paths,
why the failures failed, and every stated assumption spelled out rather than
left as a token.

### 2.3 Research memory — `packages/forge/research/knowledge.py`

Model ✓ · Backend ✓ · UI ✓ · Tests ✓ (23 + 7) · Provenance ✓

A finding an analysis produced, kept with the chain back to what produced it:
analysis, artifact, content hash, strategy, backtest, dataset, spec/code/data
hashes, evidence tier, and how many of the run's trades the claim rests on.

The dangerous object this could have been is a free-text claim with a
provenance block stapled to it — something that cites a real run and a real code
hash, looks derived, and was typed. On screen and in the database it would be
indistinguishable from a measured one, and the provenance would make it read as
*more* credible. So `promote` accepts a statement only if it appears verbatim in
the artifact's own findings. Paraphrase is refused.

It is deliberately **not** `forge.memory.research`. That store exists to prune:
it records failures and declines to spend compute on their neighbourhoods. A
finding must never prune anything. The separation from the judge is checked
structurally in both directions by walking the import graph, and an end-to-end
test asserts that promoting every finding leaves the dossier's verdict
byte-for-byte unchanged.

A finding that turns out to be wrong is retracted with a mandatory reason, not
deleted: a claim that simply vanished would leave a later reader unable to tell
it from one nobody ever made.

### 2.4 Question routing — `packages/forge/research/routing.py`

Model ✓ · Backend ✓ · UI ✓ · Tests ✓ (35 + 8)

The lab had six analyses and a dropdown. A researcher thinks *"does this edge
disappear when volatility spikes?"*, so something has to get from the sentence
to the verb.

It is a scored match against declared vocabulary rather than a model call, and
the second reason matters more than the first. A model call cannot be tested
deterministically. And a model asked "which analysis answers this?" will answer
*something* for a question none of them answer — "would this work on ES?" is not
a question about this ledger, and routing it anyway returns a real analysis of
real trades, correctly computed, addressing a different question, with nothing
on screen to say so.

So it refuses in two situations and says which. Nothing matches: it names what
the lab *can* answer. Two analyses both fit: it lists them with the words each
one heard, and running the top candidate anyway is a button the reader presses
rather than something done for them behind a confidence score.

### 2.5 Premium Graphite and the theme system

Backend ✓ · UI ✓ · Tests ✓ (14 + 45 + 32 vitest + 9 Playwright)

The workstation had a token file and one palette compiled into it. 187 colours
were written as literals across fourteen stylesheets — every one a value that
would stay the same whatever theme was selected.

`tokens.css` is now the only place a colour is named. A theme is a block that
redefines colours and nothing else; density is a separate attribute that moves
spacing and row height and deliberately leaves the type scale alone. Three
themes: Premium Graphite (default), Light / Silver, Dark / High Contrast, each
restating every colour rather than inheriting one.

Liquid Glass is a material, not a theme: one class, a named short list of
surfaces allowed to use it, and every blur radius a token so the high-contrast
theme can turn translucency down without touching a rule.

Sound is synthesised from a two-oscillator recipe — no files, nothing licensed,
nothing downloaded — off by default, and unable to break anything: no Web Audio,
a constructor that throws, an oscillator that throws mid-play, a `resume()`
rejected by the autoplay policy, all silence rather than an exception. Nothing
plays on hover, on an ordinary click, on a keystroke or on chart movement.

Settings live in the operator settings the application already keeps, so a
second machine finds the workstation you configured. One localStorage key
remains and only as a cache, so the first paint is not the default theme
flashing to the chosen one.

### 2.6 The parameter landscape — `packages/forge/research/parameter_surface.py`

Model ✓ · Data ✓ · Backend ✓ · UI ✓ · Tests ✓ (24 + 10) · Provenance ✓

The brief's first example of a 3D visualisation. The renderer, the surface
shape, the artifact store and the provenance chain all existed and had nothing
to draw, because the sweep could only move one parameter at a time.

`POST /strategies/{id}/sweep-surface` runs the grid as a job — every cell a real
backtest — and the result is the same `AnalysisResult` every other analysis
produces, so there is no second surface format.

The caveats are the feature. Every cell is in-sample by construction. The cell
count is reported as a trial count, because that is the number G5 has to be told
about. And when the best cell stands clear of its own neighbours, the result
says the peak is the shape of a fit *before* the number is read.

Driven on 36 real backtests over deliberately edge-free synthetic bars, it did
exactly that: best **+7,201.84** against a neighbouring mean of **−125.90**,
flagged. On data with no edge in it a peak is noise, and it said so unprompted.

### 2.7 A lineage that branches

UI ✓ · Tests ✓ (7 vitest)

The lineage view drew ancestors, the subject and its children as one flat
ordered list. That reads as a sequence, and a sequence is a claim: it says the
third child came after the second and that both descend from the first. Neither
is true of a search that tried four lookbacks from one parent and went deeper on
the third.

Descendants are now a tree. Ancestors stay a list, because they genuinely are a
chain.

---

## 3. Bugs found and fixed

1. **Three routes were answering from nothing.** `/verdicts/{run_id}` built a
   twelve-value P&L series in the handler, repeated it three times, asserted
   `data_gate_passed`, `preregistered` and `implementation_tests_passed` without
   reading any of them, and returned a real `Verdict` for any run id.
   `/agents/{run_id}` judged the same invented series and returned a full
   specialist debate over it. `/prop/simulations/{run_id}` drew ninety daily
   figures from a seeded normal distribution and returned a pass rate, a risk of
   ruin and a drawdown distribution. The sibling `/analysis/{run_id}` had been
   fixed in a previous session, which made this worse than any of them alone: a
   caller comparing them would find the refusal on one and conclude the others
   were answering from evidence. All three now refuse and name the route that
   reads an actual ledger.

2. **A tally of 100 reported against 1,000.** `PropSimulation.outcomes` is a
   *sample* — a hundred paths kept so a reader can inspect individual accounts.
   The route counted failure reasons from it and rendered the result under
   `fail_count`. A thousand accounts all failing on maximum loss displayed as
   "maximum loss · 100 · 10.0%": nine hundred failures missing, the percentage
   off by an order of magnitude, and the two numbers consistent with each other
   so nothing looked wrong. Three further aggregates had the same defect and
   were unused, which is the only reason they had not been noticed — including
   a `median_terminal` that could have shown a different middle outcome from the
   matrix route for the same pairing.

3. **A second palette overriding the design system.** `command.css` opened with
   its own `:root` block redefining background, text, line and brand, loaded
   after the token file and therefore winning. Found by Playwright, and nothing
   else could have: every token resolved to nothing in a cold browser while the
   application looked correct in a session that had been hot-reloading all
   afternoon. It also explains something that should have been suspicious
   earlier — the token file could be edited with very little visible effect.

4. **VaR and CVaR displayed as gains.** Both arrive as positive loss
   magnitudes. The matrix panel rendered `money(cvar_95)`, so a $2,817 tail loss
   showed as "$2,817" under the label "average of the worst 5% of outcomes" — in
   the risk column.

5. **Labels that were always true.** `simulate_prop_paths` asserted
   `SAMPLE_DATA` and `UNVERIFIED_RULES` on every simulation as model defaults. A
   run over a real strategy's real trade ledger against a verified rule set came
   back claiming both. A label that is always present carries no information and
   costs more than nothing: it teaches the reader to skip the row, taking the
   meaningful labels with it.

6. **The appearance was fetched only by the Settings screen**, so every other
   page ran on the browser cache. Change the theme, navigate away, and the old
   one came back.

7. **A parent cycle silently dropped records from the lineage tree.** Found by
   a unit test before it shipped. Anything the walk does not reach is now
   attached at the top — in the wrong place, but visible.

8. **`apps/web` had no `typecheck` script**, so `npm run typecheck` failed with
   "Missing script" rather than running `tsc`. Any check piped to `tail` looked
   like it passed.

9. **Two vocabulary defects in the router**, found by its own tests: `when` was
   weighted for time-of-day, so "is the edge worse when the market is choppy" —
   a volatility question — was refused as ambiguous; and bare `stop` was
   weighted for excursion, so "did it stop working after 2022" was too.

---

## 4. What is not done

Stated plainly, because the brief asks for it.

- **The onboarding form for `build_workspace`.** Outstanding since two
  handoffs ago. The action exists and the agent can call it; there is no form.
- **Research memory is not read back by the agent.** Findings are stored,
  searchable and shown in the lab. Nothing yet retrieves them *into* an agent's
  context when it starts work on a related strategy, which is the second half
  of what the brief describes.
- **Pine Script and NinjaScript exporters** still return a coverage report and
  no code, deliberately: AlgoForge has no TradingView and no NinjaTrader to
  check a generated script against, and the brief says not to build fake
  exporters.
- **Nothing is calibrated against NinjaTrader.** Every P&L in the system is
  modelled. This is the largest standing limitation and is not fixable from
  this machine.
- **The chart loads one 900-bar window at a time**; panning does not fetch more.
- **`Experiments.count` still includes crash-abandoned claims.** Deliberate: it
  raises the trial-count hurdle, which is the safe direction to be wrong in.

### Scaffolded and still labelled as such

DOM, order ticket, positions, orders, account, risk, replay panel, watchlist
quotes. They all still say they are unbuilt. **Do not make them draw plausible
data.**

---

## 5. Things that will bite the next session

1. **Playwright needs `ALGOFORGE_API_URL` as well as `ALGOFORGE_WEB_URL`.**
   Without it, seven workspace tests fail on a refused connection to :8765 and
   look like regressions.
2. **A stopped background server may not actually die.** Two `uvicorn` and two
   `vite` processes survived being stopped this session; the next start then
   fails to bind and the *old* build keeps answering. Always confirm a route you
   just added returns something other than 404 before concluding it is broken.
3. **`git checkout -- apps/web/src/styles/`** will silently undo the whole token
   file. It happened once here. Commit the token work before running any
   directory-wide revert.
4. **Do not run broad judge sweeps on the real workspace.** Judging consumes a
   lineage's burn-once holdout. Every run this session used an isolated
   `ALGOFORGE_HOME` under the scratchpad.
5. Windows: no heredocs over ~8KB through the Bash tool. Write a patch script
   to the scratchpad and run it.
6. Test file basenames must be unique repo-wide — the test directories have no
   `__init__.py`.

---

## 6. Standing rules followed

- Never convert INCONCLUSIVE into PASS. Never weaken a gate. G0–G13 are
  untouched; the findings layer is presentation only and cannot reach them.
- Never weaken a test to make it green. Where a test changed, it was because
  the behaviour under it was wrong: three routes stopped fabricating, one
  assertion had the VaR/CVaR inequality backwards, and one fake audio node was
  routing frequencies into a loudness assertion.
- Never draw data that does not exist. Every unbuilt panel still says so, and
  every new surface reports absence as absence rather than as zero.
- Commit and push at each stable stage. Nine commits this session, all pushed.
- Measure before claiming. Every number in this report was produced on this
  machine.
