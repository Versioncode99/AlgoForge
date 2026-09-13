# OpenAlice — reconciliation

Part 7 asks for a reconciliation against OpenAlice on thirteen named concepts,
using it as an architectural and product reference only, and warns against the
outcome the exercise most invites: turning AlgoForge into a generic AI trading
assistant.

**Researched from public material only** — the published docs and the public
repository. Nothing was copied: no code, no prompts, no branding, no proprietary
implementation. What follows is a comparison of ideas against what the
repository already does.

## What OpenAlice is, in its own terms

An AI trading agent built on a *workspace-as-operating-room* model: each serious
task gets a directory, a git repository and a durable agent CLI session. Work is
issue-shaped (markdown tasks that are simultaneously coordination objects and
execution objects), data is CLI-shaped, memory is graph-shaped (wikilinked
tracked entities forming an Obsidian-like index), and finished work lands in an
Inbox as durable reports rather than dissolving into chat history. Session
continuity runs on a stable `resumeId` so a report, an issue or a scheduled run
can return to the exact session that produced it. Irreversible actions are
approval-gated, with direct push authority off by default.

It is a genuinely good design, and it is a design for a **different product**.
OpenAlice covers equities, crypto, commodities, forex and macro, and its unit of
work is a research task about an asset. AlgoForge's unit of work is a
*hypothesis about a strategy*, carried through validation to allocation under a
deterministic gate ladder. That difference decides almost every row below.

## The thirteen concepts

| Concept | AlgoForge today | Disposition |
| --- | --- | --- |
| **Workspaces** | `forge.workstation` — panels, context, link groups, sidebar, docking | **Different meaning; do not merge.** OpenAlice's workspace is a *task container* (directory + git + session). AlgoForge's is a *view of the system* the operator arranges. Both are right for their product, and collapsing them would give AlgoForge a workspace that is neither a layout nor a task. |
| **Issues** | `ResearchFrontier` items, campaigns, missions | **KEEP_EXISTING, and it is stronger.** A frontier item carries an epistemic state — UNTESTED, INCONCLUSIVE, EXHAUSTED, NEEDS_REVIEW — which is a claim about *what is known*. A markdown ticket carries a workflow status, which is a claim about who is doing it. For research, the first is the load-bearing one. |
| **Schedules** | `research_loop`, the campaign scheduler, `StoppingCriteria` | **KEEP_EXISTING.** Already durable, already bounded by explicit termination conditions rather than by a cron line. |
| **Inbox** | `OperatingLog`, `activity`, dossiers, chat artifacts | **PARTIAL — the one real gap.** See below. |
| **Tracked entities** | `KnowledgeStore` findings, `HypothesisGraph` | **KEEP_EXISTING, and adopting the alternative would be a regression.** A wikilink graph asserts relatedness by authoring convenience. A `ResearchFinding` carries provenance, timestamp, source artifact, confidence and supersession, so "these are related" is a claim with evidence behind it. AlgoForge's whole premise is that memory is not evidence; a graph where any note may link any other erases that line. |
| **Research graph** | `HypothesisGraph` + frontier + novelty fingerprints | **KEEP_EXISTING.** Structural fingerprints over a canonical IR are a stronger notion of "we have seen this before" than backlinks between documents. |
| **Session continuity** | `ConversationStore` (durable threads), campaign `run_identity` | **CONVERGED INDEPENDENTLY.** OpenAlice's `resumeId` lets a report return to the session that produced it; AlgoForge's conversation ids plus artifact `refs` do the same job from the other end — the artifact points at the strategy, and the thread that made it persists. The campaign identity work goes further: resuming is *refused* when the configuration that gave the work meaning has changed, which `resumeId` does not attempt. |
| **Asynchronous work** | Campaign engine, job bar, orchestrator | **KEEP_EXISTING.** |
| **Agent orchestration** | `forge.research.agents` (10 roles), `routing.route()` | **KEEP_EXISTING.** Selection is per question and auditable; roles are measured by `effectiveness.py` rather than kept for appearance. |
| **Model/provider abstraction** | `model_routing`, `model_capabilities` | **KEEP_EXISTING, now stronger.** Per-role routing with declared capability and a four-class failure taxonomy landed in this phase. |
| **Approval-gated operations** | `forge.modes.permissions`, `ApprovalQueue` | **KEEP_EXISTING, and materially stronger.** OpenAlice's gate is a switch that is off by default. AlgoForge's is a pure function of actor, mode, stance and action facts, first-match-wins, with a table test pinning the whole surface. A switch can be turned on; a policy has to be edited and the edit is visible in a diff. |
| **Durable artifacts** | `Artifact` with `refs`, provenance-tagged turns | **KEEP_EXISTING, and stronger in one specific way.** An artifact carries *identifiers*, never copied numbers, so a figure can never be read from a stale message instead of from the system that computed it. A markdown report is a snapshot that is right when written and silently wrong afterwards. |

## The one real gap: work that finishes has nowhere to arrive

OpenAlice's Inbox is the idea worth taking, and AlgoForge does not have it.

AlgoForge produces durable finished work — dossiers, validation evidence,
campaign results, ported strategies — and it is all reachable. But it is
reachable by *going to look for it*. `OperatingLog` and `activity` are event
streams, which answer "what happened recently", not "what finished and is
waiting for me". An overnight campaign that completed four experiments and
produced one validation candidate says so in an event feed, in the same texture
as every heartbeat around it.

The failure mode is not lost data. It is an operator who does not know a result
exists, which for an unattended research system is most of the value.

**Not built in this phase, deliberately.** Doing it properly means deciding what
counts as finished work, who it is addressed to, and when it stops being
unread — and getting that wrong produces a notification surface nobody reads,
which is worse than the event feed that already exists. It is also the item on
this list most likely to drift toward "generic AI assistant", which Part 7
explicitly warns against: an Inbox of AI-written summaries is exactly the shape
to avoid. What would justify it is a *deterministic* arrival rule — a campaign
reached a stopping criterion, a strategy cleared G0–G13, a prop account crossed a
threshold — rather than an assistant deciding something was interesting.

Recorded as a named gap with a design constraint attached, rather than built in a
hurry at the end of a phase.

## What was deliberately not adopted

- **File-driven, git-backed workspaces.** AlgoForge's state is SQLite with schema
  versions and migrations. Markdown in git is inspectable and diffable, and it is
  also unvalidated: nothing stops a hand-edited file from claiming an experiment
  ran. For a system whose premise is that evidence is answerable, that trade goes
  the other way.
- **Issue files as execution objects.** A file that is simultaneously the task
  and the record of the task is convenient and makes provenance circular.
- **Wikilink memory.** Covered above: relatedness without evidence.
- **CLI-shaped data access.** AlgoForge's data path is a typed contract with
  coverage and freshness. Shelling out would lose both.

## The line this reconciliation is mostly about

The directive's warning is the real output here. Nine of thirteen concepts came
back KEEP_EXISTING, and in four cases adopting OpenAlice's approach would have
been an active regression against AlgoForge's own premises — provenance-bound
evidence, deterministic authority, and numbers that live where they were
computed.

AlgoForge's core stays:

```
Research → Evidence → Validation → Strategy → Allocation
        → Risk → Execution → Monitoring → Re-evaluation
```

OpenAlice is a good reference for *how durable agent work should feel* — it is
where the Inbox idea comes from, and it is right that finished work should not
dissolve into chat history. It is not a reference for what AlgoForge is.

## Sources

- [TraderAlice/OpenAlice](https://github.com/TraderAlice/OpenAlice)
- [What is OpenAlice](https://www.openalice.ai/docs/getting-started/what-is-openalice)
- [Workspaces](https://www.openalice.ai/docs/workspaces/workspaces)
- [Workspace structure](https://www.openalice.ai/docs/workspaces/structure)
- [Inbox](https://www.openalice.ai/docs/workspaces/inbox)
