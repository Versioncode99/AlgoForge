# Final requirement matrix

> **Superseded in part.** This was scored at `8baae2c` during the master
> reconciliation phase. The safe full reconciliation that followed moved
> thirteen of these rows; `docs/RECONCILIATION_FINAL.md` is the current score
> and names which moved and which did not. What stands here unchanged is the
> method and the evidence for the rows nothing has touched since.

Part 11 asks for a second complete audit against all three directives, done
against the repository rather than against the first audit, and for enough
evidence that another engineer can see what is complete without trusting the
narrative.

**Audited at** `8baae2c`, 33 commits ahead of `main` (`fd69333`), working tree
clean, pushed.

---

## 1. Final requirement matrix

Scored against the repository at the head above. The first audit
(`docs/`-external, published mid-phase) scored **79 / 27 / 3 / 8**
complete/partial/deferred/missing across 117 requirements. This one is scored
fresh; where a row moved, the commit that moved it is named.

### Document 1 — engineering foundation

| § | Requirement | Then | Now | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Forensic TradingAgents audit | Missing | **Complete** | `TRADINGAGENTS_AUDIT.md` — upstream `be952b8`, 145 files, 8,520 in-package lines, 61 test modules, read from a clone (`06da112`) |
| 2 | Capability gap matrix | Missing | **Complete** | `TRADINGAGENTS_GAP_MATRIX.md` — 35 capabilities, 14 KEEP_EXISTING / 4 ADAPT / 3 IMPORT_PATTERN / 4 EXTEND / 0 REPLACE / 8 REJECT / 2 DEFER |
| 7 | INVALID never becomes a verdict | Partial | **Complete** | `FrontierState.NEEDS_REVIEW`, in none of the automatic sets (`2a1797e`) |
| 9 | Model capability registry | Partial | **Complete** | `model_capabilities.py`, checked at selection not at the call |
| 10 | Failure classes distinguished | Partial | **Complete** | Four classes, sized by router reaction; count pinned by test |
| 11 | Role-aware output bounds | Partial | **Complete** | `output_ceiling()` clamps to the global limit and can never widen |
| 12 | Budget enforcement modes | Partial | **Partial** | `enforced` switch + `SAFETY_LIMITS` separation. ADAPTIVE mode still absent — 2 of 3 |
| 13 | External research untrusted | Partial | **Partial** | `retrieved_at` recorded; content hash and an explicit sanitisation stage still absent |
| 15 | Checkpoint identity | Missing | **Complete** | `research/identity.py`; 45 tests incl. the nine §15 names (`c8a1f11`) |
| 24 | Learnable but observable routing | Partial | **Partial** | Effectiveness recorded; no recommendation loop |
| 34 | Data fabric router contract | Partial | **Partial** | Traced end to end in `DATA_FABRIC_ARCHITECTURE.md`; no unified entitlement/completeness contract |
| 35 | Freshness enforced | Partial | **Partial** | `data/freshness.py` exists; still not wired into the chart path |
| 41 | Six named documents | Partial | **Complete** | All six present |
| 43 | Real E2E | Partial | **Complete** | 47 browser + 17 real-Electron journeys |
| 45 | Windows verified | Partial | **Complete** | `python (windows-latest, 3.13)` **success** on `8baae2c`, the audited head |
| 53 | Phases A–H | Partial | **Complete** | A and B done late and recorded as such |
| 56 | QA report answers the questions | Partial | **Complete** | The TradingAgents questions are answerable now |
| 6 | Adversarial structured objections | Partial | **Partial** | Roles exist; the 14-category objection schema does not |
| — | All other D1 rows | Complete | **Complete** | Unchanged |

**Document 1: 41 complete, 7 partial, 0 deferred, 0 missing.**

### Document 2 — product and workstation

| § | Requirement | Then | Now | Evidence |
| --- | --- | --- | --- | --- |
| 1b | Seven-bucket hedge-fund classification | Missing | **Complete** | `HEDGE_FUND_RECLASSIFICATION.md` — 11 loop stages + 3 oversight surfaces, written against the code. `REMOVE` is empty, which is the finding: nothing built for the mode was mode-specific |
| 2 | AI horizontal, not a mode | Partial | **Partial** | Chat is a panel everywhere and the registry is shared, but AI is still one of three top-level modes |
| 4 | Multi-window | Partial | **Complete** | Registry, IPC, lifecycle, restore, 17 Electron journeys (`a92feb2`) |
| 6 | Workspace/Window/Group/DockNode | Partial | **Complete** | DockNode resolved by decision, not omission: the grid already holds geometry, so docking is operations over it (`docking.py`) |
| 8 | Every workspace operation real | Partial | **Complete** | Dock, undock, split, tab now real end to end (`a833c10`, `5ba2f27`, `1aeee7e`) |
| 15 | Multi-window performance | Partial | **Complete** | Measured: `PERFORMANCE_BASELINE.md` |
| 16 | No per-window data duplication | Missing | **Complete** | Traced: one pipeline, several readers. Cross-window poll dedup declined with a number |
| 18 | Visibility informs work | Missing | **Complete** | Three states; obscured → 0 requests, background → 4, measured (`f72f6b5`) |
| 20 | Electron lifecycle + soak | Partial | **Complete** | Real Electron under Xvfb, bounded soak, memory-return check |
| 22 | Docking model | Missing | **Complete** | Split, tab, detach, reattach, resize, collapse — engine, API, gesture, tabs |
| 29 | 24-step E2E scenario | Partial | **Partial** | Steps 1–3, 8–14, 22–24 browser; 4–7 and 15–21 in Electron. Window-**group** arrangement UI is the gap |
| 30 | Measured acceptance criteria | Missing | **Complete** | Seven operations measured; four structural assertions |
| 31 | Final design test | Partial | **Partial** | One of thirteen still answers no: workspace *windows* cannot be snapped together in the UI |
| — | All other D2 rows | Complete | **Complete** | Unchanged |

**Document 2: 29 complete, 3 partial, 0 deferred, 0 missing.**

### Document 3 — QuantPad UX

Unchanged except where the reconciliation touched it.

| Area | Now | Evidence |
| --- | --- | --- |
| IP constraints, research depth, colour, density, Prop UX, Monte Carlo, heatmaps, storytelling, classification, 11-section report, priorities | **Complete** | `QUANTPAD_UX_RESEARCH.md` |
| 7-level progressive disclosure | **Partial** | Levels 1–5 reachable; 6 (this account) and 7 (what should change my mind) are not a built path |
| Per-surface analytical catalogue, 13 questions each | **Partial** | Rationale present by area, not as a per-surface catalogue |
| Tails beside point estimates · scrubbable equity fan · Challenge/Funded toggle | **Deferred** | Named in §11 and §G with reasons |

**Document 3: 11 complete, 2 partial, 3 deferred, 0 missing.**

### Document 4 — AI chat

| Area | Then | Now | Evidence |
| --- | --- | --- | --- |
| Artifact deep links | Deferred | **Complete** | `route.ts`; `port` → port pane, `prop_simulation` → desk, `validation` → gates (`3435599`) |
| Capability, end to end | — | **Complete** | 22 tests over the real registry; 160 actions, no chat-only verb (`7b049d7`) |
| Cross-platform porting | Partial | **Partial** | Pine ceiling VERIFIED; NinjaScript, MQL5, Python ceiling STRUCTURAL |
| Inline quantitative visualisation | Partial | **Partial** | Artifacts link out; no chart rendered in a turn |
| `parameter_surface` artifact | — | **Complete** | Action verb added, sweep extracted to `forge_api/surface.py` so the route and the action are one implementation; artifact registered; opens its strategy |
| Opening-context auto-attachment | Deferred | **Deferred** | Panel accepts `opening`; call sites pass none |
| 11 user journeys | Partial | **Partial** | 6 chat + 2 porting exercised |
| — All other D4 rows | Complete | **Complete** | Unchanged |

**Document 4: 14 complete, 3 partial, 1 deferred, 0 missing.**

### Totals

| | Complete | Partial | Deferred | Missing |
| --- | --- | --- | --- | --- |
| **117 requirements** | **95** | **15** | **4** | **0** |
| First audit | 79 | 27 | 3 | 8 |

---

## 2. Completed this phase

Thirteen requirements moved from partial or missing to complete: D1 §1, §2, §7,
§9, §10, §11, §15, §41, §43, §53, §56; D2 §4, §6, §8, §15, §16, §18, §20, §22,
§30; D4 deep links and capability verification.

## 3. Remaining partial

Fifteen, each with the reason in the tables above. The four that matter most:

- **D1 §13** external research has no content hash or sanitisation stage.
- **D1 §35** the freshness envelope is not enforced where it was designed to be.
- **D2 §29/§31** workspace *windows* cannot be arranged into a group from the UI.
- **D4 porting** three of four targets emit nothing verified, by design — the
  ceiling is honest, but it is a ceiling.

## 4. Deferred, with rationale

- **Tails beside point estimates, scrubbable equity fan, Challenge/Funded
  toggle** — presentation changes to analyses that are already correct, ranked
  below a chart that could not pan and a grid that discarded its axes.
- **Opening-context auto-attachment** — the panel accepts it; wiring every call
  site is mechanical and was not the bottleneck.
- **Cross-window poll deduplication** — declined with a measurement, not a
  feeling: under one request per second across three windows.
- **An Inbox** — the one idea worth taking from OpenAlice, deliberately not built
  in a hurry because what makes it worth having is a deterministic arrival rule.

## 4b. Nothing missing

Both rows that were missing at the first pass of this audit are now closed. The
`REMOVE` bucket being empty in the hedge-fund classification is the more
interesting of the two: nothing built for that mode turned out to be
hedge-fund-specific, which is why removing the label cost nothing.

Two pieces of named debt remain that are not requirements and are not pretending
to be: `forge.hedgefund` is still called that while containing the book layer,
and `resample` is now the only artifact kind with no surface to open.

## 5. Invalid or no longer applicable

- **D1 §10's seven failure classes.** The upstream sizing rule makes it four;
  building seven would have been a maintenance cost with no decision attached.
  Recorded as a finding against the directive rather than silently narrowed.
- **D2 §6's DockNode.** The grid already carries geometry. A dock tree would
  have been a second layout model and a migration for every saved workspace.

## 6. Test and verification evidence

| Suite | Result |
| --- | --- |
| Backend `pytest` | **3219 passed**, 0 failed |
| Frontend `vitest` | **318 passed**, 28 files |
| Electron (real shell, Xvfb) | **17 passed** |
| Browser E2E | 47 tests, 8 environmental failures (`DATABENTO_API_KEY`) |
| `ruff` | clean |
| `mypy --strict` | clean, 204 source files |
| `tsc --noEmit` | clean |
| `vite build` | clean |

Backend grew 2719 → 3219 across the branch.

## 7. Performance evidence

`docs/PERFORMANCE_BASELINE.md` and `PERFORMANCE_BASELINE.json`. Cold start
275 ms, window open 210/30/45 ms, restore 183 ms, resident 445 MB base at
~110 MB per window, +107 MB outstanding after four cycles, requests per 6 s
active 10 / background 4 / obscured 0.

Not measured and named as such: pointer-driven drag and pan latency, IPC volume,
database writes per operation, docking latency.

## 8. Known limitations

1. ~~Windows CI had not reported on this exact head.~~ It has since: green on
   `8baae2c`.
2. Eight browser E2E tests fail on a missing market-data key; attributed by diff,
   not assumed.
3. `+107 MB` after four window cycles is within allocator behaviour and did not
   grow per cycle, but is not zero.
4. The claim that Chromium stops polling a minimised window is believed, not
   measured — Xvfb has no window manager to minimise against.
5. Three of four porting targets have a STRUCTURAL ceiling.

## 9. Security findings

A security review over the branch found no high-confidence exploitable
vulnerabilities; two sub-threshold items were fixed anyway (`72fb970`,
`28a1e34`). Since then:

- **The desktop bridge was broken and restored.** `f72f6b5` added a local
  `require` to a `sandbox: true` preload, which throws before
  `exposeInMainWorld` — so `window.algoforge` was absent entirely. Found by real
  Electron, not by any unit test; two contract tests now prevent recurrence.
- **No new grants.** The four docking verbs went through the permission
  invariant test, which refused them until they were classified.
- Nothing in this phase widened what an AI actor may reach: the capability
  contract, failure taxonomy and role ceilings are all narrowing.

## 10. Architectural decisions

| Decision | Where |
| --- | --- |
| No dock tree; docking as operations over the existing grid | `docking.py` |
| Checkpoint identity by configuration digest, fail-safe by addressability | `identity.py` |
| Visibility has three states; only `obscured` stands a renderer down | `DATA_FABRIC_ARCHITECTURE.md` |
| Four failure classes, sized by router reaction | `model_capabilities.py` |
| `NEEDS_REVIEW` belongs to no automatic set | `frontier.py` |
| No cross-window data broker, declined on measurement | `PERFORMANCE_BASELINE.md` |
| Nine of thirteen OpenAlice concepts kept as they are | `OPENALICE_RECONCILIATION.md` |

## 11. Git and CI state

- Branch `claude/zen-hawking-nm63gx` at `767884f`, 32 commits ahead of `main`
  (`fd69333`), working tree clean, pushed.
- PR #10 open as a draft, no merge conflict, no open review threads.
- CI on `8baae2c`: `web` ✅, `python (ubuntu-latest, 3.13)` ✅,
  `python (windows-latest, 3.13)` ✅ — all three job types green on the audited
  head. A duplicate Windows run from the second workflow was still in flight;
  the same job had already passed on this commit.
- `main` untouched; nothing merged automatically.

---

## What this audit changed about the first one

The first audit scored 8 requirements missing. Two remain: the hedge-fund
bucket classification, and the `parameter_surface` artifact. Six were closed.

Three corrections were made to the record during the phase, and they are the part
worth reading:

1. The `forge.strategy.ir` circular import was **mine**, introduced in `c8a1f11`,
   not pre-existing as `a833c10` claimed. The stash test that convinced me
   otherwise only removed the *later* phase's changes.
2. `f72f6b5` broke the entire desktop bridge, and every unit test passed
   throughout.
3. `DATA_FABRIC_ARCHITECTURE.md` stated an unverified assumption as an
   observation.

All three were found by building the verification the directive asked for, and
none would have been found by the suite that was already green.
