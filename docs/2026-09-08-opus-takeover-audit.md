# Takeover audit — finishing the research-engine attack

**Date:** 2026-09-08
**Started from:** `b6c8c18`, clean, `main`
**Ended at:** `6028c32`
**Machine:** a second one. The repo lives at `C:\Users\Videe\Desktop\AlgoForge` here;
the handoff's `F:\AlgoForge` is the other PC. Same remote, same branch.

The previous session's handoff said Phase 3 was incomplete and named four areas
it had *read* and found sound but not probed: research-memory poisoning, holdout
double-consumption, lineage cycles, interrupted engine cycles. All four were
attacked. Three of them had bugs. The fourth — the holdout — genuinely held.

---

## 0. Verifying the handoff before trusting it

| Claim | Result |
|---|---|
| `HEAD == origin/main == b6c8c18`, clean | confirmed |
| 680 Python tests | confirmed |
| `ruff check` clean | confirmed |
| `mypy --strict` clean, 96 files | confirmed clean, **94** files |
| Vitest 12 passed | 11 passed, 1 failed by stopwatch on this machine — see §7 |
| Playwright 15 passed | confirmed, once browsers were installed here |
| Obsidian not required | confirmed empirically: a fresh `ALGOFORGE_HOME` resolves to `app-v2`, `obsidian_required: false`, zero configuration |

The handoff was honest. Nothing in it was found to be overstated.

---

## 1. Concurrent writers forked research memory's integrity chain

**Class:** data integrity. **Live**, in ordinary operation.

*Should happen:* each failure record commits to the one before it, so a deletion
or an edit is detectable.

*Did happen:* `record` read the chain's tail row outside the write transaction.
Eight workers read the same predecessor and committed siblings that each claimed
it. **Measured: 48 records from 8 threads, chain broken at entry 6.**

*Why it matters more than a bad report:* the engine runs exactly eight workers,
so this fires constantly. Once routine concurrency breaks the chain, a genuine
edit is indistinguishable from it — the tamper-evidence stops being evidence.

*Fix:* `BEGIN IMMEDIATE` spans the read and the write.
*Test:* `tests/memory/test_memory_adversarial.py` — 8 threads × 6 records, chain intact.

## 2. An infrastructure failure could prune a parameter region

**Class:** scientific validity. **Latent** — the engine passes a `FailureClass`
explicitly and does not call this helper yet, but it is exported as the intended
fallback for engine-level stops.

*Did happen:* `classify_reason` matched `"no trades"` before noticing the same
sentence said the dataset was unavailable, returning `NO_TRADES` — reach 0.08,
which prunes. Research memory learning *"this region is bad"* from *"the dataset
was unavailable"*, which is the exact failure the brief names.

*Fix:* infrastructure markers are checked first, and ambiguity resolves toward
the class that does **not** prune. The asymmetry is the argument: guessing wrong
that way costs one re-run; guessing wrong the other way deletes candidates that
were never tested.

## 3. `finish()` could rewrite the evidence G1 reads

**Class:** scientific validity. **Latent** — every call site passes literal
keywords today.

`finish(key, **fields)` promotes any key matching a real column, and
`preregistration_hash` is a real column that `_preregistration_holds` reads as
G1's evidence. So an *outcome* could rewrite the *claim*: run it, dislike the
result, re-freeze the hypothesis, write the new hash. The previous session closed
one route to this by binding the hash to the run; this was a second one.

Blocking column promotion alone would not have been enough. `_merge` lets the
payload blob supply any field whose column is NULL, so the same call could give
an experiment that was **never pre-registered** a hash it never had — evidence
manufactured rather than altered. Both routes are closed.

*Fix:* `finish` refuses provenance outright rather than ignoring it. It takes
arbitrary keywords, so the day someone forwards a results dict into it, silence
would corrupt the record and a stack trace would not.

## 4. Lineage cycles were constructible

**Class:** correctness / architectural.

`parent_id` is also a promotable column, so `finish(root, parent_id=child)` closed
a loop. Reads were already guarded (`seen` set, depth cap) and did not hang — but
a cycle has no root, so `roots()` returned **zero** and the whole line of enquiry
became invisible. "Where did this come from?" had no honest answer.

*Fix:* `parent_id` is frozen after reservation (§3), and `reserve` now requires
the parent to already exist. Every edge therefore points strictly backwards and
none can be redirected: **the graph is acyclic by construction**, not by the read
guards noticing in time. Those guards stay — this class is not the only thing
that can write the file — and a test now injects a cycle directly into SQLite to
prove a corrupted edge still cannot hang a reader.

A policy proposing its own parent again *declines* rather than raising: that is a
neighbourhood step that clamped back to where it started, and the existing dedup
already gave the right answer. An existing test caught the first attempt at this
and was right to.

## 5. An interrupted run deleted candidates from the search

**Class:** state management. **Live.**

Reservation is a content-derived primary key — which is what stops two workers
running the same configuration, and also what made every interruption permanent.
A worker killed between reserving and finishing left a row saying `reserved` that
nothing would ever write against, and because the key comes from the parameters,
**every later attempt at those parameters was declined as a duplicate of work
that never happened.** Measured: reserve, die holding it, restart, re-reserve →
`None`, forever. The configuration left the search silently.

*Fix:* `start` releases claims left by a process that is gone — it has already
established none of its own workers are alive — and logs the count, because a
number that is not zero means the last run did not shut down cleanly. The
recovered row keeps its identity and frozen provenance, so the retry is the same
experiment picked back up rather than a second one.

Reserving became a read-then-write, so it takes the write lock up front. Both
races are re-proved under eight threads.

**Left deliberately unfixed, for the operator to decide:** `count` still includes
claims that were abandoned and never re-proposed, so a crash-heavy history
inflates the Deflated Sharpe trial count with configurations never tried. That
direction *raises* the hurdle — the safe way to be wrong — and correcting it
makes passing easier, which is not something to change quietly on an agent's own
judgement. Recovered candidates that get re-run stop counting this way anyway.

## 6. `ResearchLedger` leaked every connection it opened

**Class:** resource / operational. `with conn:` commits but does not close;
`ResearchMemory` already used `closing()` and the ledger did not. On Windows the
open handles keep the database file locked. Fixed to match.

## 7. A fresh install opened on a data set it could not run

**Class:** UI / first-run. Found only by running the suite on a machine with no
imported archive.

The default was `is_imported && available`, falling back to `datasets[0]` — which
is the 16-year NQ archive, rendered `disabled` in that same dropdown. So a fresh
install opened with an unrunnable data set selected, Run enabled, and the first
backtest anyone tried died on a provider error saying nothing about their
strategy. The fallback now prefers any *available* data set first. Machines with
the archive are unaffected — the first branch matches there as before.

---

## The holdout held

Attacked and **not** broken, which is worth recording as strongly as the bugs:

- 16 threads racing one lineage → exactly one winner, no errors.
- 8 **processes** racing one lineage → exactly one winner. The guarantee comes
  from SQLite's locking, so a second AlgoForge instance cannot get a second look.
- Crash between consuming and attaching → still spent after restart, `result_id`
  left NULL. That is the honest record: the holdout was spent and what it bought
  was lost.
- Separate lineages do not contend.
- A result cannot attach to a lineage that never consumed.

Both consumption sites (the engine and the operator's judge route) consume
*before* executing, so a retry cannot turn an observed result into another tuning
round. `tests/research/test_holdout_adversarial.py`.

---

## Verification

| | Before | After |
|---|---|---|
| Python tests | 680 | **705** |
| `ruff check` | clean | clean |
| `mypy` | clean (94 files) | clean (94 files) |
| Vitest | 11/12 here | **12/12** |
| Playwright | not re-run since the Python changes | **15 passed / 4 skipped** empty workspace; **17 / 2** seeded |

The two Playwright failures drive a real backtest on real bars, which this
machine cannot produce: the purchased NQ archive is on the other PC, and the
alternative needs a paid Databento key. Environment, not regression — and
deliberately not papered over with a skip.

**Every e2e run used an isolated `ALGOFORGE_HOME`.** No real holdout was near it.

---

## What is next

Phase A and B are done. The scientific layer has now been attacked in every area
the handoff listed as unprobed, and the remaining known-unfixed item (§5, the
trial count) is a decision rather than a defect.

The workstation build is next, and the ordering argument has not changed: the
Workspace Engine and the shared action registry come before charting, because
charting is the first thing that should be *built as a panel* rather than
retrofitted into one. The action registry already exists for research verbs
(`apps/api/forge_api/actions.py`); extending it to workspace verbs is what makes
the agent an operator rather than a parallel implementation.
