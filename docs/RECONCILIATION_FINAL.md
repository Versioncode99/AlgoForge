# Safe full reconciliation — final report

Phases 0–14 of the reconciliation directive, scored against the repository at
`5d05a75`, 48 commits ahead of `origin/main` (`fd69333`), working tree clean.

Nothing was force-pushed, reset, rebased or deleted. Every branch that existed
when this started still exists at the SHA `docs/BRANCH_RECONCILIATION.md`
records.

---

## 1. Branch reconciliation — nothing to consolidate

`docs/BRANCH_RECONCILIATION.md` has the full table. In one line: seven of the
ten branches are literal ancestors of `origin/main`, one is byte-identical to
it, one is this working branch, and one — `feat/industry-standard-validation` —
shares **no common ancestor with main at all**.

That last one read as the worst possible finding and was the opposite. Main's
root commit is that branch's tip plus one 96-file overhaul; only seven paths
exist there and nowhere in main, and each was settled by reading the module and
grepping its consumers rather than by its name. All three deletions among them
were deliberate and are written up in documents main already carries. It is kept
and not merged: it is the only record of 2026-09-01 to 09-07, which main's
history cannot reach.

**No merge, no cherry-pick, and so no conflict that could have been resolved
mechanically.**

## 2. What moved this phase

| Requirement | Was | Now | Where |
| --- | --- | --- | --- |
| D1 §6 — structured adversarial objections | Partial | **Complete** | `forge/agents/objections.py` — all fourteen threats answered on every review, four of them honestly outside the ladder with the experiment that would close them |
| D1 §12 — three budget modes | Partial (2 of 3) | **Complete** | `BudgetMode`; `ADAPTIVE` applies nothing under the soft threshold and everything above it, and **enforces when the spend cannot be read** |
| D1 §13 — external research untrusted | Partial | **Complete** | Content hash, invisible-character sanitisation *with the removals reported*, and a fence with one real call site |
| D1 §24 — learnable but observable routing | Partial | **Complete** | `routing_observations.py`; eight fields recorded, twenty observations before a comparison, no code path from a recommendation to a setting |
| D1 §34 — data fabric contract | Partial | **Complete** | `tracked` block naming §34's seven and where each is reported, with `reported` measured rather than asserted |
| D1 §35 — freshness envelope | Partial | **Complete** | `docs/FRESHNESS_ENFORCEMENT.md` — the determination §35 asks for, plus the one consumer where the answer was "yes, and it is missing" |
| D2 §29/§31 — window groups in the UI | Partial | **Complete** | A Windows section in the workspace manager; an Electron test drives it by clicking and asks the main process whether it happened |
| D3 — tails beside point estimates | Deferred | **Complete** | Pass interval beside the pass rate; payout distribution behind the mean |
| D3 — scrubbable equity fan | Deferred | **Complete** | Over every path, closed accounts carried forward, `live` reported beside the band |
| D3 — Challenge/Funded unified | Deferred | **Complete** | Simulated as one account's history; a test asserts it is *not* the product of the two rates |
| D4 — artifact destinations | Partial | **Complete** | Every kind resolves; `resample` got a pane, a deep link and an action verb |
| D4 — opening-context attachment | Deferred | **Complete** | "Ask about this" on the strategy, one subject chosen deterministically |
| D2 §30 — performance dimensions | 4 unmeasured | **3 measured** | IPC volume, docking latency, writes per operation. Drag/pan latency still is not, and says so |

## 3. What is still partial, and why

Each of these is a real gap, stated rather than reclassified.

| Requirement | State | What is actually missing |
| --- | --- | --- |
| **D2 §2 — AI horizontal, not a mode** | Partial | Chat is a panel everywhere and the action registry is shared, so the *capability* is horizontal. AI is still one of three top-level modes, because the mode is what decides an assistant's permissions and removing it would mean removing the boundary, not the label. Closing it properly is a permissions-model change, not a menu change. |
| **D3 — progressive disclosure levels 6–7** | Partial | Levels 1–5 are reachable. Level 6 ("what does this mean for *this* account") and level 7 ("what would change my mind") are not a built path. Level 7 is the more interesting of the two and the structured objections are half of it — a reader can now see which of fourteen threats would have to change. |
| **D3 — per-surface analytical catalogue** | Partial | The thirteen questions are answered by area in `QUANTPAD_UX_RESEARCH.md`, not as a per-surface catalogue. |
| **D4 — inline quantitative visualisation** | Partial | Artifacts link out to the surface that computed them. Nothing renders a chart inside a conversation turn. |
| **D4 — cross-platform porting** | Partial, **structurally** | Pine is VERIFIED. NinjaScript, MQL5 and Python emit nothing verified, and that is a ceiling rather than a gap: verifying them needs an execution engine for each platform, which this build does not have and does not claim. |
| **D4 — eleven user journeys** | Partial | Six chat journeys and two porting journeys are exercised end to end. |

## 4. Declined, with the measurement

**Cross-window poll deduplication.** Re-checked against new evidence rather than
restated: an active renderer issues ~11 requests per 6 seconds, a background one
4, an obscured one 0, and IPC from an idle window is **zero calls over six
seconds**. Three windows against a local API is under one request per second in
total. Brokering those through the main process would add a parallel data path —
which Doc 2 §28 forbids without cause — to save something that is not costing
anything. Deferred with a number attached, for the second time and with a
sharper number.

**Panel drag and chart pan latency.** Still unmeasured; measuring it meaningfully
needs frame timing rather than a wall clock around an IPC call. What is known
now is the server half: a docking operation is single-digit milliseconds and four
row writes, so a slow drag is in the browser and not behind the API.

## 5. Bugs this phase found — none of which a passing test had caught

1. **A launch could erase the saved session before anything restored it.** The
   main window's placement fires `move`/`resize`, both of which persist the
   arrangement, and at that moment no workspace window is open. Restore failed
   about one run in three depending on whether the debounce landed first.
2. **A workspace window ignored the workspace it was opened for.** The shell
   loads `#workspace?workspace=<id>` and says in its own comment why; the view
   read `/workspaces/active` regardless, so two windows showed the same
   workspace. Most of what multi-window is for, absent.
3. **A settings mode loaded from disk silently behaved as `ENFORCED`.** `StrEnum`
   members compare equal to their string and are not identical to it, and every
   decision was an `is` check. It presents as the setting not saving.
4. **`create_strategy_from_blueprint` recorded only the blueprint id**, so a
   strategy that certainly existed produced an artifact card that could only say
   "no panel yet".
5. **The mode chooser said "four operating environments"** for as long as the
   Hedge Fund mode had been gone. The count comes from the manifest now.
6. **Two ways of counting database writes measured nothing.** `PRAGMA
   data_version` reported a flattering zero writes per operation. Both attempts
   are written up in `PERFORMANCE_BASELINE.md`, because both looked like they
   worked.

## 6. Verification

| Suite | Result |
| --- | --- |
| Backend `pytest` | **3356 passed**, 0 failed |
| Frontend `vitest` | **410 passed**, 37 files |
| Electron, real shell under Xvfb | **20 passed** — four consecutive clean full-suite runs after the flakes were taken to root cause |
| `ruff` | clean |
| `mypy --strict` | clean, 209 source files |
| `tsc --noEmit` | clean |
| `vite build` | clean |

No test was weakened, skipped or deleted to reach this. Four tests changed
shape because the thing they tested changed shape — the budget switch became
three modes, the window list gained `self` — and each change is in the commit
that caused it.

One near miss worth recording: a scripted edit to `chat-panel.test.tsx` silently
removed thirteen tests, and the count in the suite output is what caught it. The
file was restored and the edit redone.

## 7. Git and CI

- Branch `claude/zen-hawking-nm63gx` at `5d05a75`, **48 commits ahead** of
  `origin/main` (`fd69333`), 0 behind, working tree clean, pushed.
- PR #10 open as a draft. No merge conflict.
- `main` untouched. Nothing was merged into it.

## 8. The ambiguity, reported rather than resolved

The directive asks that main become the canonical verified tree and Phase 14
asks for a final main SHA. Doc 1 §54 says *"do not merge into main
automatically unless explicitly instructed"*, and this session's branch
requirement says never to push to another branch without permission.

`origin/main` therefore stays at `fd69333` and everything stays on
`claude/zen-hawking-nm63gx`. Merging PR #10 is the step that makes main
canonical and is left to the repository owner on purpose.
