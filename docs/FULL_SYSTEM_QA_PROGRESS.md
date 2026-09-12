# Full-System QA — progress

Branch: `feat/autoresearch-v2`. Running log. Each entry records what was swept,
how, what it found, and what was done. An area is "clean" only when a sweep ran
and produced a number; "no finding" without a sweep is recorded as **not yet
swept**, because the difference between the two is the whole value of this file.

Severity is about consequence, not effort:

| | meaning |
|---|---|
| **P0** | wrong evidence could reach a verdict, or money is at risk |
| **P1** | the system stops doing its job, or an operator's work is lost |
| **P2** | a fact is unavailable, misreported, or silently discarded |
| **P3** | dead weight — no wrong behaviour, but the code says something untrue |

---

## Area log

### A1 · Dead code and unreferenced symbols — swept
**Method.** AST sweep over `packages/` and `apps/api/` for every module-level
`def`/`class`, cross-referenced against imports, attribute access, string
references (registry names arrive as strings) and the frontend bundle.

**Finding.** No unreachable public symbol. The three candidate clusters —
`forge.capabilities`, `forge.explain`, `forge.provenance` — are each reached
through the action registry by name, which a naive import sweep misses.
Recorded so the next sweep does not re-raise them.

### A2 · Fields written and never read — swept, 2 found
**Method.** Every declared model field, cross-referenced against reads in the
backend, the tests and `apps/web/src`. 148 candidates; `model_dump`
serialisation accounts for most of the false positives.

- **P3 — `Observation.was_profitable`.** Computed by the director from
  `net_pnl` on every observation, read by no rule. Removed, with the work that
  filled it. → `de623da`
- **P2 — `ResearchPlan.freshness_requirement`.** Mine, added two commits
  earlier, defaulted to `FRESH` and enforced by nothing: configuration
  accepted and thrown away. Now enforced by the plan gate — an unknown state is
  refused by name, and a plan declaring it will accept `STALE` or `DEGRADED`
  *as evidence* is refused outright, matching `Served.admissible` at the data
  layer. `REFRESHING` is admissible: past TTL with a refresh under way is still
  the last good value. → `de623da`

This is the third instance of the same defect shape in this repository
(`link_group`, the campaign dates, this one), which is why A3 exists.

### A3 · Configuration accepted and discarded — swept, 1 found
**Method.** Every constructor and API parameter that is stored, traced to a
read.

- **P1 — `Campaign.start_date` / `end_date`.** Stored since the campaign store
  was written, read by nothing. Every campaign silently researched the most
  recent ~9 months regardless of what the operator asked for. Now drives
  `Campaign.time_scope()` and the engine's window. → `9ae30a7`, `f995f4f`

### A4 · Silent fallbacks and swallowed exceptions — swept, 1 found
**Method.** AST sweep for `except` handlers whose body neither re-raises, logs,
nor records; 75 silent handlers, 14 catching bare `Exception`.

**Finding.** 74 carry a same-line justification the detector does not parse
(`except Exception:  # an agent bookkeeping failure must not stop research`)
and are correct: a bookkeeping failure must not stop the loop. One was not.

- **P2 — `_mechanism_for_artifact` collapsed three facts into one.** A corrupt
  split receipt, a missing dataset and a load failure all returned `None`,
  which is also what a run stored *before* receipts existed returns. `None` is
  the right outcome — G9 reads it as INCONCLUSIVE, never as a pass — but the
  operator could not tell "never measured" from "the store is damaged". The
  outcome is unchanged; the failure is now named in the log with the dataset
  and the exception. → `8eccb17`

### A5 · Concurrency: races and lost updates — swept, 1 found
**Method.** Every store with a read-modify-write cycle, checked for a lock
spanning both halves rather than each half.

- **P1 — `CampaignStore` lost updates.** `set_status`, `archive`, `restore`,
  `prioritise`, `rename`, `record` and `set_progress` each read a whole row,
  mutated it and wrote it back, holding the lock over neither end. A worker's
  `record()` could write `status='paused'` over an operator's resume.
  Reproduced deliberately: without the lock, **13 of 100** concurrent
  increments were lost. Fixed by holding the lock across read *and* write.
  → `a19719f`, `tests/research/test_campaign_concurrency.py`

### A6 · Look-ahead and evidence leakage — swept, 1 found (P0)
**Method.** Every path from bars to a judged verdict, checked for a split whose
partitions come from a different window than the bars they judge.

- **P0 — scoped bars judged against default-window partitions.** `_cycle`
  resolved `self._partitions or chronological_split(bars, …)`. `ResearchPartitions`
  holds actual bar *lists*, cut from the engine's default window, so a campaign
  running on a 2-year scope would have been judged against out-of-sample bars
  drawn from a different period. Partitions now travel with the bars they were
  cut from. → `fb69632`

  The regression test needed strengthening twice. The first version loaded
  once and so only exercised the cache-miss branch; re-introducing the leak in
  the cache-hit branch left it green. It now loads twice, and was confirmed to
  fail with the leak restored and pass without it.

### A7 · UNKNOWN ≠ ZERO — swept
**Method.** Every place a missing measurement becomes a number, and every
default on a metric field.

**Finding.** The rule holds at the gate: G9 reads `None` as INCONCLUSIVE, never
as a pass, and the freshness envelope's `NOT_EVIDENCE` set keeps `STALE` and
`DEGRADED` out of a verdict. The one violation found was of *observability*
rather than arithmetic and is A4 above.

### A8 · The engine stops on a single bad candidate — swept, 2 found
- **P1 — one generated template stopped the whole campaign.**
  `gen_trend_strength_gate` asks for 1007 warm-up bars against a shipped
  maximum of 520, so `chronological_split` raised for that candidate — and,
  because the catalogue-wide purge is computed once, for every cycle after it.
  `_split_for` now tries the catalogue-wide purge, falls back to the
  candidate's own warm-up with a log, and returns `None` — a named
  `Outcome.BLOCKED` — when neither fits. Crashed cycles 5 → 0. → `bec0f98`
- **P1 — a screened candidate produced no research at all.** `_observe` sent no
  `failure_class`, so `_generate_followups` returned immediately on every
  screening failure. `_screen_failure` now classifies: NO_TRADES,
  INSUFFICIENT_SAMPLE, NEGATIVE_EXPECTANCY — with the sample problem
  outranking the losing number, because "it lost over 6 trades" is a statement
  about the sample and not about the edge. Follow-ups 1 → 2, experiments
  3 → 7. → `eed9416`

### A9 · Warm-up honoured on every data path — swept, 1 found
- **P2 — `load_range` ignored `pad_bars` on the non-imported branch.** A
  dataset that was not imported got no warm-up, silently, so indicator state
  began mid-series. → folded into `8df0c8c`

### A10 · Test doubles that no longer match — swept, 1 found
- **P2 — `tests/agents/test_command.py` stubbed `_cycle(*args)`.** The keyword
  `partitions` broke it, and the engine loop swallowed the `TypeError`, so the
  test failed by *timeout* rather than by error. Fixed to `*args, **kwargs`.
  → `480121d`

### T1 · Secrets to every sink — swept, clean
**Method.** Sweep of every log, telemetry, exception, `print` and HTTP-error
line for a secret-shaped identifier within 24 characters; plus the git history
for key material and high-entropy literals; plus every `os.environ` read whose
name says credential.

**Findings, all negative and each verified:**
- 9 sink lines mention a credential — every one names the *variable*
  (`"DATABENTO_API_KEY is not set"`), never a value.
- No tracked file is key material. `.env.example` is present and every value in
  it is empty; `.gitignore` covers `.env` and `.env.*` with an `!.env.example`
  exception.
- No high-entropy literal in tracked source (`sk-`, `ghp_`, `AKIA`,
  `BEGIN PRIVATE KEY`).
- `forge.propdesk.credentials.Secret` refuses `__reduce__`, so a credential
  cannot be pickled or serialised into a record; `__repr__` and `__str__` both
  redact; `reveal()` is the only exit and is named to be greppable.
- `CredentialMaterial` exposes `present: bool` and nothing else to any caller
  outside the module that reads it.
- The AI prompt path (`assistant.py:251`) serialises the question, the instance
  context and action *results*. No registered action returns credential
  material; the only credential-adjacent one, `credential_present`, returns one
  bit.

**P3 — noted, not fixed.** `.env.example` still lists `NVIDIA_NIM_API_KEY`
after the NIM provider was removed. Harmless, and correcting it is a
one-line change somebody should make deliberately rather than as a QA
side-effect.

### T2 · Can an AI actor widen its own permissions? — swept, 1 found
**Method.** Every one of the **154 registered actions** evaluated against the
real policy for `Actor.AI`, across all four modes and every stance — the
inverse of the shipped test, which only checks that allowlists name actions
that exist.

**The escape route is closed, and this is now measured rather than asserted:**
`PROTECTED reachable by AI: none`. `HIGH risk reachable by AI: none`. Rule 2
denies every protected control in every mode and stance; rule 3 denies every
high-risk action; rule 9 holds anything nobody classified.

- **P2 — twenty verbs added by the last two phases were never classified.** The
  campaign, sidebar and workstation-context verbs fell through to rule 9. The
  *default is closed*, so nothing became unsafe — it became unavailable.
  The assistant those verbs were written for could not set the context it was
  about to discuss, or start the campaign it had just designed, without a human
  approving each call. Classified from the policy's own stated rationale:
  sidebar and context verbs join the panel verbs in `PREPARATORY` (the policy
  already says rearranging panels is not consequential in any mode); campaign
  lifecycle joins `start_engine` in `AUTOMATION` (it commits the machine to
  unattended work); `create_campaign` stays preparatory beside
  `create_strategy`, because creating a campaign commits nobody to anything and
  starting one does. `reset_sidebar` and `archive_campaign` carry CONFIRM risk
  and remain held by rule 5, before any list is consulted.

  The gap existed because only one direction was tested. Both directions are
  tested now: `test_every_registered_action_has_a_decided_ai_ruling` pins the
  fall-through set, so a verb that joins it without somebody writing down why
  fails the suite. Verified to fail when a classification is removed.


### P1 · Research Control Center metrics — swept, 2 found
**Method.** Every number the control centre renders, traced to what computes
it, and the label it is rendered under read against what that computation
means.

- **P2 — "Health 100%" on a campaign that had run no cycles.**
  `CampaignRuntime.health()` scores a campaign with no history 1.0 deliberately,
  so an unproven campaign is allocated the workers it needs to earn a real
  score. That prior is correct where it lives and is not a measurement. The card
  rendered it as a percentage — on the card an operator watches immediately
  after pressing start. The scheduler is unchanged; the runtime now reports the
  sample behind the score and the card says "no cycles yet" until there is one.
  → `f65d4ab`
- **P2 — "Mechanisms" counted wrong in both directions.** This is the number
  the interface leans on to separate a hundred discoveries from one idea a
  hundred times. It was summed from each campaign's cached progress counter,
  which is refreshed only when a cycle runs — so between cycles it lagged the
  graph, and three campaigns that had each proposed a hypothesis reported
  `Mechanisms: 0`. Once refreshed it is worse: a mechanism is a *string*
  deduplicated by the graph, so three campaigns exploring one explanation each
  honestly report 1 and sum to **3**. Measured directly before fixing. The
  hypothesis graph has answered this question system-wide since it was written;
  it is asked now. → `a91e6f5`
- **P3, same commit.** Totals were computed over a 200-row page ordered by
  priority, so a sum labelled "Experiments" quietly omitted the campaigns below
  the cut. Cards are still a page; totals are over every campaign.

### L1 · Agent roles: distinct, or only differently named? — swept, 3 found
**Method.** 20,000 bucket draws per role against the default campaign
allocation, comparing the resulting workload distributions. A role's entire
mechanical effect is a bias on that draw, so two roles with the same
distribution are one role with two names.

| | before | after |
|---|---|---|
| named roles | 10 | 10 |
| distinct behaviours | **9** | **10** |

- **P2 — DISCOVERY and FEATURE were identical to three decimal places.** They
  held the same two buckets in opposite order, and the draw tests membership
  rather than order. An operator reading "Proposes mechanisms nothing on record
  already claims" and "Investigates how a signal is constructed" and deploying
  one of each got two discovery agents, with nothing saying so. FEATURE now
  prefers DISCOVER_FAMILY and ADVANCE_PROMISING, read off `BUCKET_KIND` rather
  than chosen: those produce `SearchKind.FAMILY` and `SearchKind.STRUCTURAL`,
  which is what "how a signal is constructed" means in this vocabulary.
  REFINE_PARAMETERS stays out because the role's own sentence ends "not how it
  is tuned". → `915f64e`

**Two findings recorded rather than changed**, because both would alter what
the research does and that is not a QA side-effect to make unasked:

- **P2 — the LITERATURE role does not cause literature retrieval.**
  `_maybe_retrieve` is gated on `campaign.web_research`, for every role alike. A
  LITERATURE agent on a campaign with retrieval off retrieves nothing; a
  DISCOVERY agent on a campaign with it on retrieves. The role's sentence —
  "Retrieves published evidence and keeps its provenance" — describes something
  the role does not cause.
- **P3 — a SPECIALIST agent's objective reaches no decision.** It is stored,
  returned by the API and shown as a tooltip. `ROLE_BUCKETS[SPECIALIST]` is
  empty by design, so a specialist draws the campaign's allocation untouched
  and its free-text objective changes nothing. Making the objective steer the
  search would require model inference in the allocation path, which the
  deterministic-policy rule argues against; the honest options are to say so on
  the surface or to drop the role, and both are the operator's call.

### B1 · Every GET route on a cold installation — swept, clean
**Method.** All 102 API GET routes called against an empty installation inside
the app's real lifespan, path parameters filled with ids that do not exist.

| | |
|---|---|
| routes exercised | **102** |
| answered | 80 |
| named 404 | 22 |
| 5xx or unhandled exception | **0** |

Every 404 carried a machine-readable reason rather than a bare status.

### B2 · Every read-only action on a cold installation — swept, clean
**Method.** All 42 read-only actions that take no required argument, called
through the registry as a HUMAN actor against an empty installation.

Five raised `ActionError` — `describe_context`, `describe_sidebar`,
`describe_workspace`, `workspace_history` and `prop_account_status` — each
naming the remedy ("No workspace is open. Create one with create_workspace, or
pass workspace_id to say which you mean."). That is the designed refusal, not a
defect. The remaining 37 returned a dictionary. The 18 read-only actions that
require an argument were not called and are listed in the report.

### B3 · The engine and the research loop, actually running — swept, clean
**Method.** The real app started inside its lifespan, a campaign created and
started over HTTP, the engine left to run, then the same surfaces an operator
would ask.

```
engine before start:  STOPPED  | not started
after 4 cycles:       RUNNING  | 4 worker(s) processing
after stop:           STOPPED  | stopped
```

4 cycles · 3 experiments · 3 hypotheses · 3 mechanisms · 23 journal events ·
1 skip, classified useful · `last_error: None` · 0 errors.

Campaign runtime reported `health=0.75 observed=4` against recent outcomes of
3 PROGRESS and 1 NOT_NOVEL — the share is right and the sample behind it is now
visible. `/api/v1/engine` and `/api/v1/engine/diagnostics` agreed on the state
at every point, which was asserted rather than assumed.

### S1 · CI, cross-platform — inspected
`.github/workflows/ci.yml` runs Python 3.13 on **ubuntu-latest and
windows-latest** plus a Node 24 web job. The Windows leg is what catches the
path and line-ending assumptions this codebase can make on Linux alone. Branch
status is on the pull request.

### Q1 · Is the discovery ontology truthful? — swept, 1 ceiling found
**Method.** The archetype vocabulary and the shipped template catalogue read
directly; 1,200 compositions built across all 10 archetypes and their entry
signatures compared.

```
archetypes                                   10   (all 10 structurally distinct)
shipped templates                            12   (7 distinct feature sets)
name overlap                                  1   (opening_range_break)
compositions built                        1,200
distinct composed shapes                    280   (28 per archetype)
distinct ENTRY SIGNATURES reached            10
```

**No defect. A ceiling.** A composed template's key carries the definition hash,
so composition can mint up to 280 distinct *keys* — but `Archetype.signature()`
is the feature set plus the entry condition's structure tokens, and ignores
exits and sessions by design, so all 280 collapse to 10 constructions. That is
the mechanism behind the 67.5% NOT_NOVEL rate measured earlier: 78 of 81
refusals were `SAME_CONSTRUCTION`. The gate is doing exactly its job on a
vocabulary that has run out.

**This corrected a claim in this repository's own earlier report.**
`RESEARCH_EFFECTIVENESS_REPORT.md` said the engine "genuinely creates new
constructions" and cited 1.3 configurations per construction. The ratio stands;
what was never measured was the size of the space it is computed over. A
120-cycle campaign reached 22 distinct template keys against a vocabulary of
roughly 17-21 constructions — essentially all of it. §2.1 of that report now
carries the measurement and the amended conclusion.

---

## Not yet swept

- **N** — product QA against the six personas
- **O** — quality of life

Both are product judgement rather than defect-finding. They are not attempted,
and saying so is the point: an area is reported clean only where a method and a
number are recorded for it.

## Standing verification

`ruff check .` clean · `mypy --strict` clean across 189 source files ·
**2,697 backend tests, 0 failures** · 110 frontend tests across 14 files.
