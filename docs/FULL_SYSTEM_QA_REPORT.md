# Full-System QA — report

Repository-wide audit of AlgoForge on `feat/autoresearch-v2`, covering the work
of the three preceding phases and the code they were built on. The running log,
with the method and the raw numbers for each area, is
[`FULL_SYSTEM_QA_PROGRESS.md`](./FULL_SYSTEM_QA_PROGRESS.md). This document is
the summary: what was found, how bad it was, and what happened to it.

Every finding below was reproduced before it was fixed, and every fix was
confirmed to fail against the code as it was. Where a sweep found nothing, the
sweep and its count are stated, because "clean" without a method is an opinion.

---

## 1. What was found

**Fourteen defects, one of them P0.** Four more findings are recorded and
deliberately not changed, for reasons given against each (§9).

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | **P0** | Scoped bars were about to be judged against default-window partitions | fixed `fb69632` |
| 2 | P1 | Lost update in `CampaignStore`: a worker could un-resume a campaign | fixed `a19719f` |
| 3 | P1 | Campaign `start_date`/`end_date` stored since inception, read by nothing | fixed `9ae30a7` |
| 4 | P1 | One generated template stopped every subsequent cycle | fixed `bec0f98` |
| 5 | P1 | A screened candidate produced no research at all | fixed `eed9416` |
| 6 | P2 | "Mechanisms" — the anti-inflation metric — counted wrong in both directions | fixed `a91e6f5` |
| 7 | P2 | "Health 100%" on a campaign that had run no cycles | fixed `f65d4ab` |
| 8 | P2 | Twenty verbs reached the AI actor through no classification | fixed `f65d4ab` |
| 9 | P2 | Two agent roles were one role with two names | fixed `915f64e` |
| 10 | P2 | A corrupt receipt was indistinguishable from a legacy run | fixed `8eccb17` |
| 11 | P2 | `load_range` ignored `pad_bars`, so warm-up was silently skipped | fixed `8df0c8c` |
| 12 | P2 | A test double no longer matched the signature it doubled | fixed `480121d` |
| 13 | P3 | Two model fields written and never read | fixed `de623da` |
| 14 | P2 | The proposal refusals an agent corrects against named nothing specific | fixed, §8 |

Recorded, not changed:

| Severity | Finding | Why not changed |
|---|---|---|
| P2 | The LITERATURE role does not cause literature retrieval | Would change what the research does |
| P3 | A SPECIALIST agent's objective reaches no decision | The two honest fixes are both product calls |
| P3 | `.env.example` lists a provider that was removed | A deliberate one-line change, not a QA side-effect |
| — | The construction vocabulary is ~17-21 wide and one campaign exhausts it | A ceiling, not a bug. See §4 |

## 2. The one that mattered

**Scoped bars, default-window partitions.** The AutoResearch V2 work gave each
campaign its own time scope. `_cycle` resolved its train/test split as
`self._partitions or chronological_split(bars, …)`, and `self._partitions`
holds *actual bar lists*, cut from the engine's default window. A campaign
running on a two-year scope would have had its candidates judged against
out-of-sample bars drawn from a different period entirely.

Nothing would have said so. The verdict would have been produced, stored,
preregistered and rendered exactly as a sound one.

Partitions now travel with the bars they were cut from. The regression test
needed strengthening twice: the first version loaded once and so only exercised
the cache-miss branch, and re-introducing the leak in the cache-hit branch left
it green. It now loads twice and was confirmed to fail with the leak restored.

This defect was introduced by this phase's own work, which is the argument for
running the sweep on your own changes rather than only on inherited code.

## 3. The pattern underneath five of them

Five findings are the same defect wearing different clothes: **a value that is
correct where it is computed and untrue where it is used.**

- The scheduler's benefit-of-the-doubt health prior, rendered as a measurement.
- A per-campaign distinct count, summed into a system-wide "distinct" total.
- A cached progress counter, read as though it were live.
- `None` from a missing receipt, reported as `None` from a corrupt one.
- Two roles with the same preference set, presented under different sentences.

None of these is a crash, and no test was going to catch any of them, because
each function is individually correct. They are caught by asking what the
*caller* believes the value means — which is why the sweeps here are organised
by question rather than by module.

A sixth, related instance is the one this repository has now shipped three
times: **configuration accepted and discarded** (`link_group`, the campaign
dates, and `ResearchPlan.freshness_requirement`, which was mine and two commits
old when the sweep caught it). A field sweep for "written, never read" is worth
running on every phase.

## 4. What the audit says about the product, not the code

Part Q asked whether the strategy-discovery ontology is truthful. The
measurement is in `RESEARCH_EFFECTIVENESS_REPORT.md` §2.1, and it amended that
report's own headline.

The engine is genuinely not a parameter-variation machine: 1.3 parameter
configurations per construction, close to the floor. But the space it searches
is **ten archetypes plus twelve shipped templates**, and a single 120-cycle
campaign reached 22 distinct template keys — essentially all of it. Composition
can mint up to 280 distinct template *keys* (direction × session × exit style),
and the novelty gate correctly collapses all of them back to **10 entry
signatures**, because `Archetype.signature()` ignores exits and sessions by
design.

That is the mechanism behind the 67.5% refusal rate: 78 of 81 NOT_NOVEL
refusals were `SAME_CONSTRUCTION`. The gate is not broken — it is doing exactly
its job, on a vocabulary that has run out.

**The highest-leverage improvement available to this product is growing the
archetype vocabulary.** Every other research number is bounded by it, and no
amount of loop tuning, allocation adaptation or agent-crew composition moves a
ceiling set by ten hand-written constructions.

## 5. Security

Nothing was found. The sweeps and their counts:

- **Secrets to every sink.** Nine log, telemetry, exception and HTTP-error
  lines mention a credential; every one names the *variable*
  (`"DATABENTO_API_KEY is not set"`), never a value. No tracked file is key
  material, no high-entropy literal appears in tracked source, `.gitignore`
  covers `.env` and `.env.*` with an explicit `!.env.example`, and every value
  in `.env.example` is empty. `Secret` refuses `__reduce__`, redacts in both
  `__repr__` and `__str__`, and exposes exactly one greppable exit.
  `CredentialMaterial` gives callers one bit, `present`.
- **The AI prompt path** serialises the question, the instance context and
  action results. No registered action returns credential material; the only
  credential-adjacent one returns a boolean.
- **Can an AI actor widen its own permissions?** All **154 registered actions**
  evaluated against the real policy for `Actor.AI`, across every mode and every
  stance. `PROTECTED reachable by AI: none.` `HIGH risk reachable by AI: none.`
  This is now measured on every run rather than asserted in a docstring.

The one finding here was a coverage gap, not a leak: twenty verbs added by the
last two phases fell through to the closed default, so the assistant they were
written for could not use them. They are classified now, from the policy's own
stated reasoning, and **both** directions of the allowlist are tested — the
shipped suite only checked that lists name actions that exist.

## 6. Runtime

The system was started and watched rather than read about.

| | |
|---|---|
| GET routes exercised on a cold install | **102** |
| 5xx or unhandled exceptions | **0** |
| named 404s (nonexistent ids) | 22 |
| read-only actions called with no arguments | 42 |
| unhandled failures among them | **0** (5 named `ActionError` refusals, by design) |

A campaign created and started over HTTP, left to run:

```
engine before start:  STOPPED  | not started
after 4 cycles:       RUNNING  | 4 worker(s) processing
after stop:           STOPPED  | stopped

4 cycles · 3 experiments · 3 hypotheses · 3 mechanisms · 23 journal events
1 skip, classified useful · last_error: None · 0 errors
campaign runtime: health=0.75 observed=4  (3 PROGRESS, 1 NOT_NOVEL)
```

`/api/v1/engine` and `/api/v1/engine/diagnostics` were asserted to agree on the
state at every point rather than assumed to.

## 7. Verification

```
ruff check .                     clean
mypy --strict                    clean, 189 source files
pytest (backend)                 2,719 passed, 0 failed
vitest (frontend)                121 passed, 16 files
tsc --noEmit                     clean
CI                               ubuntu-latest + windows-latest, Python 3.13; Node 24 web job
```

## 8. The product, used

Six personas were walked against the running API — each a sequence of things
that person must be able to do, not a list of opinions. **Zero findings.**

The step worth naming is the last: a second application was constructed over
the same state, and the campaign, its status and every total came back
identical. An unattended research run that cannot survive a restart is a run
whose results you cannot trust, and this is now checked rather than assumed.

Two answers stood out as already right, and both are the product of earlier
phases rather than this one: an operator asking *"is it working, or merely
alive?"* gets a `working` field distinct from `running`, and an operator asking
*"why is it producing nothing?"* gets a skip ledger with every refusal
classified useful, wasted or neutral.

**One quality-of-life defect fixed.** `AgentService.propose` refused a bad
experiment proposal with `Unknown template`, `Unknown parameter`,
`Parameter outside declared range` and `Parameter outside declared grid`. These
reach the operator as `proposal_rejected` and are what the proposer is expected
to correct against — and none named the template, the parameter, the bound it
missed, or a value that would work. They now carry the specific fact that fixes
them, including the nearest on-grid value, and "cited no source" is told apart
from "cited a source that was not retrieved", which used to arrive as one
sentence.

One measurement here is worth reporting against itself: the first refusal sweep
flagged 161 messages as unhelpful, and the number was wrong.
`'panel_ids' must be a non-empty list of panel ids` **is** the remedy; a check
that cannot see that is measuring its own regex. The corrected figure is 81, and
most of those name an internal wiring cause a reader cannot act on regardless.

## 9. Open decisions

The four recorded-not-changed findings in §1 are decisions, not oversights.
Each would change what the product does, and none is a QA side-effect to make
unasked:

- Whether the LITERATURE role should cause retrieval, rather than
  `campaign.web_research` deciding it for every role alike.
- Whether a SPECIALIST agent's objective should steer its work, or whether the
  surface should say that it does not.
- Whether to drop `NVIDIA_NIM_API_KEY` from `.env.example` now the provider is
  gone.
- **Whether to grow the archetype vocabulary** — §4, and by a wide margin the
  most consequential of the four.
