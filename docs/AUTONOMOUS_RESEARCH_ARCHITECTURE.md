# Autonomous Research Architecture

How AlgoForge decides what to research next, how it knows whether it is
actually researching, and what it is structurally unable to do.

---

## 1. The loop

```
RuntimeMonitor          ResearchOrchestrator        ResearchDirector (per campaign)
   heartbeats      ←──      assigns a campaign   ──→   draws a bucket
   outcomes                 to each worker             builds a candidate
   diagnosis                observes health            checks novelty
        │                          │                   records the question
        └──────────┬───────────────┘                          │
                   ▼                                          ▼
            AutonomousEngine._cycle  ──────────────→  memory gates
                   │                                   (prune, reserve)
                   ▼
        pre-registration → strategy → conformance → determinism
                   ▼
        chronological split → development → validation grid
                   ▼
                 Judge  (G0–G13)          ← the only thing that decides
                   ▼
        PromotionQueue → holdout (burn-once) → StrategyLibrary
                   ▼
            director.observe → frontier, hypotheses, follow-ups
```

Everything from the memory gates down is the pre-existing scientific
infrastructure, unchanged. The research layer chooses *what* to run; it has no
route to *what a result means*.

## 2. Runtime state

`forge.research.runtime` holds the engine's condition. It is **derived**, never
set: workers report heartbeats and outcomes, and the state is read from them.

| State | Means |
| --- | --- |
| `STARTING` | Threads created, data loading, no cycle finished yet. |
| `RUNNING` | A worker made progress inside the no-progress window. |
| `PAUSED` | Every worker is held by the operator. |
| `IDLE` | Alive, healthy, nothing progressed and nothing is wrong. |
| `WAITING_FOR_WORK` | Asking for work; the queue keeps coming back empty. |
| `WAITING_FOR_DATA` | Blocked on a dataset. |
| `WAITING_FOR_AGENT` | Blocked on a model or specialist. |
| `BLOCKED` | Something is stopping work that will not clear itself. |
| `EXHAUSTED` | The research reachable from here has been searched out. |
| `STOPPING` / `STOPPED` | Leaving, or left. |
| `ERROR` | Could not continue. |
| `RECOVERING` | Reclaiming work left by a failed run. |

**The invariant:** `RUNNING` is returned only when a worker reported
`Outcome.PROGRESS` within `NO_PROGRESS_SECONDS`. No other code path can produce
it. `EngineState.running` still exists and still means "threads exist"; the
interface reads `runtime_state` and `working`.

### The watchdog

`RuntimeMonitor.diagnose()` runs on worker 0 at the end of every cycle and on
every `status()` call. Its checks are ordered by authority — an explicit stop
beats an error, an error beats a stall — and it names the specific failure:

| Code | Diagnosed from |
| --- | --- |
| `duplicate_loop` | ≥25 consecutive duplicate/restatement refusals. |
| `duplicate_pressure` | Duplicates dominate but the run is short. |
| `no_work` | The frontier returned nothing eligible. |
| `exhausted` | The campaign reported a stopping criterion. |
| `blocked` | A capability the dataset cannot serve. |
| `cycle_errors` | Half the recent cycles raised. |
| `workers_dead` | Every worker stopped reporting. |
| `idle_no_outcomes` | Workers beating, no cycle finishing — wedged. |

Each carries a remedy sentence. It logs the **transition**, not the state, so an
unattended run leaves one line saying when it stopped progressing and why.

### Pacing

`RuntimeMonitor.backoff(base)` grows the wait between cycles towards 30s as
barren cycles accumulate, and returns to `base` the instant anything progresses.
Pacing is not reporting: a backed-off engine reports the same state, for the same
reason, as one spinning at full rate.

## 3. The skip ledger and the novelty hierarchy

`forge.research.skips`. Every refusal is classified twice.

**By gate** (`SkipKind`): `EXPERIMENT_CLAIMED`, `FAILURE_REGION`,
`TEMPLATE_CONDEMNED`, `NOT_NOVEL`, `NO_ELIGIBLE_WORK`, `CAPABILITY_BLOCKED`,
`CAMPAIGN_EXHAUSTED`, `CLAIMED_BY_AGENT`, `PROPOSAL_ERROR`. The first four
declined a real experiment and count as compute saved; three others refused
nothing and are symptoms.

**By closeness** (`NoveltyLevel`):

```
EXACT_DUPLICATE → NEAR_DUPLICATE → SAME_CONSTRUCTION → SAME_MECHANISM
  → RELATED_HYPOTHESIS → NOVEL_HYPOTHESIS → NOVEL_MECHANISM → NOVEL_FEATURE
```

`SAME_CONSTRUCTION` is **not** refused by default. Refining parameters inside a
known construction is ordinary research; what bounds it is the budget, not the
duplicate gate. Only `EXACT_DUPLICATE` is never retryable; every other band
carries the condition under which a retry is permitted, and the ledger's
`retryable()` is a list of open research questions.

Each row records the subject, the reason, the matched prior object, the
similarity, the band, the retry rule and an occurrence count. Identical refusals
collapse onto one row: eight workers refusing the same proposal is one fact.

### Deduplication that does not prevent discovery

The novelty gate refuses a *family* proposal that restates a known explanation —
correctly. But the verdict it returns carries a downgrade in its own words
("this is a new hypothesis within a known explanation, which is worth testing"),
and the director now **pursues the downgrade** rather than spending the cycle on
nothing. Only a verdict of `PARAMETER` — the same claim about the same mechanism
— refuses the cycle outright.

## 4. Failure-driven research

`forge.research.followup.derive` reads a finished cycle's failure class and gate
and produces follow-up questions: an inverse-direction hypothesis from a
trend-strength gate that failed G4, a shorter or longer horizon, a regime
filter, a feature substitution, a replication request. Each is put through the
novelty gate before being admitted to the frontier, so a failure cannot generate
an endless family of near-identical restatements.

## 5. What the research layer cannot do

- It cannot construct a verdict. `director._Gate` is a three-field shim with
  exactly the fields `outcome_from_verdict` reads, so the module is unable to
  build anything the judge would accept as evidence.
- It cannot write arbitrary code. A composed template is a
  `StrategyDefinition` — data — rendered by the existing exporter and put through
  the same static guard and smoke test as an operator-written one.
- It cannot lower a gate. Priority allocates workers; nothing downstream reads it.
- It cannot reach the burn-once holdout except through `ResearchLedger.consume`,
  whose unique primary key is the authority.
