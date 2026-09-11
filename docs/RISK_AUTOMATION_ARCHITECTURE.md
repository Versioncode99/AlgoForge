# Risk Automation Architecture

The three risk modes and autonomous deployment were designed and built before
this change. Their reference document is
**[`2026-09-11-risk-autonomy-architecture.md`](2026-09-11-risk-autonomy-architecture.md)**,
which covers the scaling law, hysteresis, cooldowns, emergency de-risking, the
disclosure and consent model and the audit schema in full.

This file records the audit's findings and this change's (small, deliberate)
contribution.

---

## 1. What the audit found

| Mode | Meaning | Where |
| --- | --- | --- |
| `MANUAL` | The operator sets the number. | `propdesk/risk.py` |
| `ADAPTIVE` | AlgoForge calculates it from account state. | `propdesk/scaling.py` |
| `AI_MANAGED` | AlgoForge manages it inside the operator's boundaries. | `propdesk/autonomy.py` |

All three move exactly one number: the share of an account's buffer that an
allocation may cost. Everything from there to an order is deterministic.

**The ceilings hold in all three modes.** Hard prop limits, the operator's
configured maximum risk, the pre-trade gate, G0–G13 eligibility, provider
restrictions and the protected controls are checked after the mode has produced
its number, not before, and none of them reads the mode.

Autonomous deployment has three settings (`OFF`, `APPROVAL_REQUIRED`,
`FULLY_AUTONOMOUS`) and cannot bypass validation, prop policy, the risk engine
or the pre-trade gate in any of them. In this build it additionally cannot reach
a venue at all: the lifecycle refuses the deployed stage while no broker
connector exists, and **reports that refusal as a mandatory control that did not
pass** rather than silently succeeding.

Consequential-action disclosures are versioned, acknowledgement is recorded with
its disclosure version, and the audit log records user, account, action, previous
state, new state, risk configuration, strategy, validation state, policy,
automation mode, reason, timestamp and approval. No secret is written to it.

## 2. What this change did

**Composability only.** `risk`, `risk_management` and `ai_management` are in the
sidebar catalogue, so a risk surface can sit in any workspace — including a
research one, which is where an operator watching a campaign approach a
deployment decision would want it.

Nothing about the risk engine, the scaling law, the consent flow or the audit
schema was altered.

## 3. The boundary this change was careful about

The research fabric introduced two new sources of authority — campaign priority
and agent roles — and **neither reads or is read by anything in risk**:

- **Priority** allocates the engine's worker threads. Nothing downstream of the
  scheduler reads it. A campaign at priority 100 gets more compute and exactly
  the same gates.
- **Roles** bias which allocation bucket a proposal is drawn from. A role cannot
  veto a bucket, cannot alter a threshold, and has no representation anywhere
  past the director.

The separation is checkable rather than remembered. `ResearchOrchestrator`
imports only `forge.research.agents`, `forge.research.campaign` and
`forge.research.runtime` — nothing from `forge.propdesk`, `forge.risk` or
`forge.judge`. `forge_api.director` imports no part of the judge and holds a
three-field `_Gate` shim precisely so it cannot construct anything the judge
would accept as a verdict.

One qualification, stated because the broader claim would be wrong:
`forge.research` *does* import `forge.judge.statistics` in three places
(`cpcv.py`, `validation.py`, `walkforward.py`). That module is pure statistical
arithmetic — per-period Sharpe and its relatives — with no gate, no threshold and
no verdict in it. Sharing the arithmetic is correct; what matters is that no
research module can import `Judge` or build a `JudgeInput`, and none does.
