# Branch reconciliation

Phase 0–1 of the safe full reconciliation. This is a **read-only forensic
record**: nothing was merged, cherry-picked, rebased, force-pushed or deleted to
produce it. Every branch listed below still exists at the SHA recorded here.

## Frozen state

| Fact | Value |
| --- | --- |
| Recorded at | 2026-09-13 |
| `origin/main` | `fd6933333aa88753a5c0ca9033a8329fa1e3aef0` — *Research expansion: a vocabulary the engine can run out of ideas before it runs out of (#9)* |
| Local `main` | `94c8a9e` — one commit behind `origin/main`, never pushed to |
| Working branch | `claude/zen-hawking-nm63gx` at `06336b4`, tree clean, in sync with its remote |
| Remote branches | 10 (incl. `main`) |
| Local branches | 2 (`main`, `claude/zen-hawking-nm63gx`) |
| Tags | none |
| Stashes | none |
| Worktrees | one (the checkout itself) |
| Other refs | none — `git for-each-ref` returns only the branches above |

Branches carrying commits **not reachable from `origin/main`**: three.
`claude/algoforge-research-expansion-s1sznx` (8, content-identical to main),
`claude/zen-hawking-nm63gx` (35, this branch's work),
`feat/industry-standard-validation` (47, **unrelated history** — see below).

## Method

Names were not trusted. For each branch:

- `git merge-base --is-ancestor <branch> origin/main` — is every commit already in main?
- `git rev-list --count origin/main..<branch>` — how many commits are unique?
- `git diff --name-only origin/main <branch>` — how much of the *tree* differs, which is
  the question that actually matters when a branch was squash-merged and so has
  unique commit objects but no unique content.
- `git ls-tree -r --name-only` set difference — which **files** exist only on the branch.
- For anything unique, the module was read and its consumers grepped for, because a
  file that exists and a capability that runs are different claims.

## The table

| BRANCH | BASE | UNIQUE WORK | ALREADY IN MAIN | SUPERSEDED | VALUABLE | OBSOLETE | ACTION | REASON |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `claude/zen-hawking-nm63gx` | `fd69333` (main tip) | 35 commits, the four-directive reconciliation | No | No | **Yes** | No | **KEEP** | This branch. Open as draft PR #10. Ahead 35 / behind 0. |
| `feat/industry-standard-validation` | **none — unrelated history** | 47 commits, 2026-09-01 → 09-07, the original ten-wave build | Content: yes. History: no. | Yes, by `main`'s root commit `2645645` | Historically | Operationally | **KEEP (archive)** | Main's own root is this branch's tip plus one overhaul commit. See the analysis below. |
| `claude/algoforge-research-expansion-s1sznx` | `94c8a9e` | 8 commits | **Yes — `git diff origin/main <branch>` is empty** | Squash-merged as PR #9 → `fd69333` | Already landed | No | **ALREADY_IN_MAIN** | Unique commit *objects*, zero unique *content*. The 8 commits are the pre-squash history of main's tip. |
| `feat/autoresearch-v2` | `7f79e00` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `401fe2a` is a commit **in** main's linear history (PR #8 merged 2026-09-12). |
| `feat/openterminal-workstation-integration` | `a7d9893` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `b6b5f8c` is in main's history (PR #7 merged 2026-09-12). |
| `claude/nifty-ritchie-1rf1tk` | `2156413` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `24b9491` is in main's history (PR #6 merged 2026-09-12). |
| `claude/wonderful-goldberg-twi9xv` | `87ee367` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `5656a19` is in main's history (PR #5 merged 2026-09-11). The Prop Desk. |
| `claude/cloud-environment-overview-o7an7t` | `c0c6247` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `4874307` is in main's history (PR #4 merged 2026-09-10). |
| `claude/four-mode-workspace-system-ndvaqr` | `8de85a6` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `1c0d9da` is in main's history (PR #3 merged 2026-09-10). Note: the Hedge Fund mode it introduced was later removed on purpose (`32f2445`), which is a supersession of *content*, not of the branch. |
| `claude/algoforge-refactor-ui-aht1p6` | `e9e0421` / `8de85a6` | 0 vs main | Yes (ancestor) | — | Already landed | No | **ALREADY_IN_MAIN** | Tip `80b70c9` is in main's history (PRs #1 and #2). |

**No branch is marked MERGE, CHERRY-PICK, OBSOLETE-and-delete, or INVESTIGATE.**
Seven of the ten are literal ancestors of `origin/main`; one is byte-identical to
it; one is this working branch; one is the pre-history, analysed next.

## `feat/industry-standard-validation` — the one branch that needed real work

This is the only branch a name-based reading would have got wrong in either
direction. `git merge-base origin/main origin/feat/industry-standard-validation`
returns **nothing**: the two histories share no commit. Read naively that is the
worst case in the whole reconciliation — 47 commits and 224 files with no
ancestry into main at all.

It is in fact the opposite. Main is this branch's continuation with the history
truncated:

- The branch's tip is `fd40876`, 2026-09-07, *"docs: README describes the engine
  and judge as they now behave"*.
- Main's **root** commit is `2645645`, 2026-09-07 **23:36**, *"feat: G0 and G1
  stop being literals on the operator's judge route"* — not an "initial commit",
  and it arrives with 250 files already in the tree.
- `git diff fd40876 2645645` is a coherent 96-file overhaul: 7,104 insertions,
  1,056 deletions. It reads exactly as one commit's worth of work on top of the
  branch tip, including `R071 packages/forge/oracles/nautilus.py →
  packages/forge/capabilities/nautilus.py` — a rename git detected across the
  two histories.

So the content lineage is continuous and only the git lineage is not. Comparing
the trees directly, **seven paths exist on the branch and nowhere in main** (main
has 587 files to the branch's 224, so it is otherwise a strict superset):

| Path | Verdict | Evidence |
| --- | --- | --- |
| `packages/forge/oracles/nautilus.py`, `packages/forge/oracles/__init__.py`, `tests/oracles/test_nautilus.py` | **Not lost — renamed** | Present as `packages/forge/capabilities/nautilus.py` and `tests/capabilities/test_nautilus.py`, with `oracle_id` → `capability_id` and `role="OPTIONAL_EXECUTION_SEMANTICS_ORACLE"` → `role="INSTALLED_PACKAGE_PROBE"`. The new docstring says why: it reports whether a package is importable, and the old name "invited the reading that something independently checks execution realism. Nothing does." |
| `packages/forge/memory/store.py`, `tests/agents/test_agents.py` | **Deliberately deleted, and the good part kept** | `DecisionMemory` is an append-only hash-chained partitioned memory — and grep across the branch shows it is imported by **one file, its own test**. It holds entries in a Python `list`, so nothing survived a restart either. Main's `docs/2026-09-07-evolution-report.md` §4 records the decision: *"`DecisionMemory`'s one real idea — a hash chain — moved to `ResearchMemory`, where it matters far more."* `forge/memory/research.py` in main chains its rows and exposes `verify_chain()`. |
| `packages/forge/sweep/engine.py`, `packages/forge/sweep/__init__.py` | **Deliberately deleted, and replaced by a real one** | `ArraySweepEngine` thresholds a numpy returns array; it never runs a strategy. Main's `docs/2026-09-07-architecture-audit.md` §2 calls it "a toy" imported only by its own test. The capability now lives in `forge.research.parameter_surface` + `apps/api/forge_api/surface.py`, where **every cell of the grid is a real backtest** (`tests/api/test_sweep_surface.py`). |

All three removals were made in main's root commit and are written up in two
documents that main itself carries. This was a reconciliation somebody already
performed deliberately; re-importing any of it would be restoring dead code over
a documented decision.

One loose end found while checking this, recorded rather than fixed here: main
still carries `prompts/waves/04-sweep-judge.md`, which names `ArraySweepEngine`
by class. The class is gone on purpose and the capability is better served by
`forge.research.parameter_surface`, so the prompt is a stale specification rather
than an unmet requirement. It is not one of the four directives under audit and
is not counted as a gap; it is noted so the next reader of that file is not
misled by it.

**ACTION: KEEP, do not merge, do not delete.** Its value is as the only record of
waves 1–10 — `git log` on main cannot reach 2026-09-01 to 09-07 at all. That is a
real gap in main's history and this branch is the thing that closes it. It has no
pull request and never had one.

## Phase 2 conclusion

> *"The target is NOT 'maximum number of commits'. The target is 'maximum amount
> of correct, coherent, tested functionality.'"*

**There is nothing to consolidate.** Every branch other than this working one is
either already an ancestor of `origin/main`, byte-identical to it, or superseded
by main's own root commit with the supersession documented in main. No merge, no
cherry-pick, and no conflict resolution was required — which means no opportunity
arose to resolve one mechanically with `--ours`/`--theirs`.

The only work not in main is the 35 commits on `claude/zen-hawking-nm63gx`, and
those are in open draft PR #10 awaiting review rather than stranded.

## One ambiguity, reported rather than guessed

The directive asks that *"main should become the canonical verified tree"* and
Phase 14 asks for a final main SHA. Two standing rules point the other way:
Doc 1 §54 — *"Do not merge into main automatically unless explicitly
instructed"* — and this session's branch requirement, *"NEVER push to a different
branch without explicit permission."*

Merging PR #10 is therefore **not** performed here. `origin/main` stays at
`fd69333` and all work stays on `claude/zen-hawking-nm63gx`. Merging #10 is a
one-click action for the repository owner and is the step that makes main
canonical; it is being left to them on purpose, not overlooked.
