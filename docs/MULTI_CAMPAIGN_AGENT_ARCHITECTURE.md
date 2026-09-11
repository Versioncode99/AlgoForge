# Multi-Campaign and Multi-Agent Architecture

## 1. Why one campaign was not a real constraint

`CampaignStore.set_status` used to refuse a second running campaign:

> Two campaigns would share the engine's data split and consume each other's
> burn-once holdout, so only one runs at a time.

That was not true of the code. `ResearchLedger` is keyed by **strategy
lineage**, and two campaigns produce different strategies and therefore different
lineages. Nothing in `consume` could collide.

What they genuinely shared was the **duplicate-claim namespace**.
`Experiments.reserve` keyed a claim on `(scope, template, parameters)`, and the
scope was `dataset:max_bars:data_version:catalog_version`. Two campaigns on one
dataset shared it, so whichever got there first silently consumed the
configuration for the other — the exact opposite of "each campaign establishes
its own evidence".

## 2. What changed, and what deliberately did not

**Claims are per campaign.** `Experiments` carries a `campaign_id` column and
includes it in the claim identity when set. Omitted when empty, so a standalone
engine's keys are byte-identical to before.

**The trial count is not.** `Experiments.count(scope)` stays dataset-wide, and
the judge calls it that way. Every trial run against this series is a trial
whoever ran it; narrowing the count per campaign would lower the Deflated
Sharpe's best-of-N hurdle exactly as more campaigns searched the same data —
the opposite of what multiple-testing control is for.

This is the single most important line in the change: **the thing that is
partitioned is the right to *try*, and the thing that is not is the count of
*tries*.**

## 3. The orchestrator

`forge.research.orchestration.ResearchOrchestrator` is a scheduler and nothing
else. It decides which campaign a worker serves. It does not decide what to
research — the campaign's own director does — and it cannot decide what a result
means. A scheduler that could also weigh evidence would be a way to give a
favoured campaign an easier gate.

```
weight = max(1, priority) * health
health = share of the last 60 cycles that produced something, floored at 0.15
```

Priority is the operator's intent; health is observed. Multiplying them means a
high-priority campaign that has stopped producing yields ground — slowly, never
all of it — to one that is producing.

**Every running campaign keeps at least one worker.** A campaign at zero workers
produces nothing, and a campaign producing nothing never recovers its health, so
starving one completely is a decision that could not be revisited.

An unproven campaign scores 1.0 for its first window: scoring it zero would deny
it the workers it needs to earn a score.

## 4. Agents

`forge.research.agents`. Before this, "agents" meant two unrelated things and
neither was a research worker: `forge.agents.skills` holds LLM role contracts,
and the engine's "workers" were threads whose research policy was a function of
their **thread index**.

### Roles

| Role | What it is for |
| --- | --- |
| `DISCOVERY` | Proposes mechanisms nothing on record already claims. |
| `LITERATURE` | Retrieves published evidence and keeps its provenance. |
| `FEATURE` | Investigates how a signal is constructed, not how it is tuned. |
| `HYPOTHESIS` | Turns a mechanism into a claim that can be shown false. |
| `FALSIFICATION` | Attacks the candidates that look best. |
| `REGIME` | Asks whether an effect only exists under a condition. |
| `ROBUSTNESS` | Stress-tests what survived until it breaks. |
| `VALIDATION` | Runs formal validation on candidates that earned it. |
| `REVIEWER` | Critiques lineage and evidence quality. |
| `SPECIALIST` | A role the operator defined. |

A role **biases** which allocation bucket is drawn and can never veto one. Four
falsification agents must not silently mean a campaign that never proposes
anything, with nothing saying so.

### States

`CREATED → STARTING → RUNNING → IDLE / WAITING / BLOCKED → COMPLETED / FAILED /
STOPPED`. `COMPLETED` and `FAILED` are outcomes and are never overwritten by
`STOPPED`: an agent that spent its budget is not the same as one that was told
to stop.

### Claims and leases

An agent claims a subject before spending compute on it. The claim is unique per
`(campaign, subject, replica_of)` and carries an **expiry**: an agent that dies
releases its work when the lease lapses rather than when the process restarts.

Replication stays possible and stops being accidental — a replica is a separate
claim with `replica_of` set, so "we ran it twice" can never be mistaken for two
independent findings.

### Capacity

`capacity_for(requested)` returns what the installation can actually run — two
agents per core, capped at 64 — and a sentence explaining any shortfall. Asking
for 32 on a four-core machine returns 8 and says why. An interface that rendered
32 idle rows would be claiming compute that does not exist.

## 5. Persistence and recovery

| What | Where | Survives restart |
| --- | --- | --- |
| Campaigns, progress, priority, crew size | `campaigns.db` | ✅ |
| Agents, heartbeats, claims | `research-agents.db` | ✅ |
| Refusals, with matched objects | `research_skips.db` | ✅ |
| Frontier, hypotheses, journal, sources, promotion | their own files | ✅ |
| Experiments and their lineage | `experiments.db` | ✅ |
| Scheduler health estimates | in memory | ❌ (rebuilt in one window) |

On start-up `build_control_router` stops campaigns left running by a dead
process, stops their agents, and releases every lapsed claim.

**The resumability bug this found:** `_catalog_version` hashed all of
`TEMPLATES`, which the director adds to as it composes. Every restart after a
campaign generated a template therefore began a *fresh* experiment scope and
re-ran everything. It now hashes `SHIPPED_TEMPLATE_KEYS` only — a generated
template needs no representation there, because its key is derived from its
composition, so a different composition is already a different key.
