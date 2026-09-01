from __future__ import annotations

from forge.agents.models import AgentClaim, AgentRole
from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel


class DebateReport(FrozenModel):
    debate_id: str
    run_id: str
    roles: tuple[AgentRole, ...]
    claims: tuple[AgentClaim, ...]
    dissent_present: bool
    numeric_verdict_locked: bool = True
    labels: tuple[str, ...] = ("DEMO_NARRATIVE", "RESEARCH_ONLY")


def build_demo_debate(run_id: str, verdict_id: str) -> DebateReport:
    roles = (
        AgentRole(role_id="RESEARCHER", can_read_holdout=False, can_change_numeric_verdict=False),
        AgentRole(role_id="CRITIC", can_read_holdout=False, can_change_numeric_verdict=False),
        AgentRole(role_id="RISK_AUDITOR", can_read_holdout=True, can_change_numeric_verdict=False),
        AgentRole(role_id="ARBITER", can_read_holdout=True, can_change_numeric_verdict=False),
    )
    claims = (
        AgentClaim(
            role_id="RESEARCHER",
            stance="SUPPORT",
            statement="The sample has positive expectancy, subject to the deterministic judge.",
            evidence_ids=(verdict_id,),
            confidence=0.62,
        ),
        AgentClaim(
            role_id="CRITIC",
            stance="OPPOSE",
            statement="The sample size and synthetic provenance prevent promotion.",
            evidence_ids=(verdict_id,),
            confidence=0.91,
        ),
        AgentClaim(
            role_id="RISK_AUDITOR",
            stance="CAUTION",
            statement="Tail estimates remain unstable until real OOS trades are imported.",
            evidence_ids=(verdict_id,),
            confidence=0.88,
        ),
    )
    return DebateReport(
        debate_id=stable_id("debate", {"run_id": run_id, "verdict_id": verdict_id}),
        run_id=run_id,
        roles=roles,
        claims=claims,
        dissent_present=any(claim.stance == "OPPOSE" for claim in claims),
    )
