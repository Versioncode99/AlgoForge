# Prop Desk Architecture

The Prop Desk was designed and built before this change. Its reference document
is **[`2026-09-10-prop-desk-architecture.md`](2026-09-10-prop-desk-architecture.md)**,
which sets out the provider model, account identity, the order state machine,
reconciliation, the copy engine and the policy states in full. That document
remains authoritative and is not restated here.

This file records what the audit found in that area, what this change did to it,
and what it deliberately did not touch.

---

## 1. What the audit found

`packages/forge/propdesk` is 12,265 lines across 22 modules and was found to be
real rather than aspirational:

| Concern | Where | State |
| --- | --- | --- |
| Provider / platform / venue separation | `identity.py`, `fabric.py` | complete |
| Declared adapters that refuse every command | `adapters/declared.py` | complete |
| Deterministic simulated adapter, fills labelled simulated | `adapters/simulated.py` | complete |
| Order state machine, partial fills, duplicate events | `orders.py` | complete |
| Reconciliation after disconnect | `reconcile.py` | complete |
| Copy engine: leaders, followers, groups, sizing, mapping | `copy.py` | complete |
| Firm policy with an `UNKNOWN` that never becomes `ALLOWED` | `policy.py` | complete |
| Allocation of validated strategies to accounts | `allocation.py` | complete |
| Manual / adaptive / AI-managed risk with hard ceilings | `risk.py`, `scaling.py` | complete |
| Autonomous deployment with consent and disclosure | `autonomy.py`, `consent.py` | complete |
| Audit trail of consequential actions | `audit.py` | complete |

**No fake integrations were found.** Rithmic, Tradovate and ProjectX are
*declared* — their interfaces are described and every command against them is
refused — and only the local simulator executes, labelling every fill as
simulated. There is no live-order path anywhere in the application.

## 2. What this change did

**Integration, not rebuilding.** Every Prop Desk destination is in the sidebar
catalogue, so a research workspace can hold prop accounts and a prop workspace
can hold a research campaign without switching modes. That is the whole of the
change to this area, and it is deliberate: the brief asked for the Prop Desk to
be *integrated*, and an area found working is not improved by being rewritten.

Concretely, these routes are now composable into any workspace:

```
desk · allocation · copy · limits · news · desk_activity
risk_management · ai_management · account · rules · drawdown · daily · target
```

`link_account_to_workspace` associates an account with a workspace. It is a
link and not a grant: it puts the account on that screen and changes nothing
about whether it is connected or what may be done with it.

## 3. What this change did not touch

The pre-trade gate, the risk engine, the kill switch, the policy states, the
consent flow, the disclosure versions and the audit log are all untouched. The
research fabric has no route into any of them — an orchestrator that could
allocate workers *and* relax a prop limit would be the single most dangerous
object in the system, so the scheduler's authority stops at deciding which
campaign a worker serves.

## 4. The loop, end to end

```
Research (campaigns, agents, frontier)
   → Validation (G0–G13, burn-once holdout)
      → Strategy Library
         → Allocation (per account, deterministic eligibility)
            → Pre-trade gate
               → Execution (simulated; declared adapters refuse)
                  → Monitoring and reconciliation
                     → Reallocation
```

Research can propose at every arrow. It can decide at none of them.
