"""Specialist review of a verdict, derived from the verdict.

What this replaced returned four hardcoded claims with fixed confidences —
0.62, 0.91, 0.88 — and fixed sentences, for every run it was ever asked about.
It was labelled ``DEMO_NARRATIVE`` internally, which was honest, but it was
served from a live endpoint as though it were analysis of the run named in the
URL. Nothing in it was.

Everything here is a function of the ``Verdict`` passed in. Four specialists
read the same gate ladder from different angles and reach their own position:

* **RESEARCHER** — is there a measured effect at all?
* **CRITIC** — what is the weakest link, and did anything actually fail?
* **RISK_AUDITOR** — what does the loss side look like, and is the sample big
  enough to say?
* **ARBITER** — does the evidence, taken together, support promotion?

Disagreement is preserved rather than resolved. Two specialists reading the same
verdict and reaching opposite positions is the useful output; forcing them into
consensus would throw away the only thing a panel adds over a single number.

None of them can move a number. Confidence here is *confidence in the stated
position*, derived from how decisive the underlying gates are — it is not a
probability that the strategy makes money, and it never feeds back into the
verdict. The judge is deterministic and stays that way.
"""

from __future__ import annotations

from forge.agents.models import AgentClaim, AgentRole
from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.judge.models import GateResult, Verdict

# Gates each specialist is responsible for reading.
_STATISTICAL_GATES = ("G5", "G11", "G12", "G13")
_RISK_GATES = ("G8", "G3")


class DebateReport(FrozenModel):
    debate_id: str
    run_id: str
    roles: tuple[AgentRole, ...]
    claims: tuple[AgentClaim, ...]
    dissent_present: bool
    numeric_verdict_locked: bool = True
    labels: tuple[str, ...] = ("DERIVED_FROM_VERDICT", "RESEARCH_ONLY")


def _by_gate(verdict: Verdict) -> dict[str, GateResult]:
    return {gate.gate: gate for gate in verdict.gates}


def _statuses(verdict: Verdict, gates: tuple[str, ...]) -> list[GateResult]:
    found = _by_gate(verdict)
    return [found[name] for name in gates if name in found]


def _metric(verdict: Verdict, name: str) -> float:
    value = verdict.metrics.get(name)
    return float(value) if isinstance(value, int | float) else 0.0


def _roles() -> tuple[AgentRole, ...]:
    return (
        AgentRole(role_id="RESEARCHER", can_read_holdout=False, can_change_numeric_verdict=False),
        AgentRole(role_id="CRITIC", can_read_holdout=False, can_change_numeric_verdict=False),
        AgentRole(role_id="RISK_AUDITOR", can_read_holdout=True, can_change_numeric_verdict=False),
        AgentRole(role_id="ARBITER", can_read_holdout=True, can_change_numeric_verdict=False),
    )


def _researcher(verdict: Verdict) -> AgentClaim:
    net = _metric(verdict, "net_pnl")
    trades = int(_metric(verdict, "trades"))
    sharpe = _metric(verdict, "sharpe_per_trade")
    p_value = _metric(verdict, "permutation_p_value")

    if net <= 0:
        return AgentClaim(
            role_id="RESEARCHER",
            stance="OPPOSE",
            statement=(
                f"No effect to defend: {net:+.2f} net over {trades} trades. "
                "There is nothing here to promote."
            ),
            evidence_ids=(verdict.verdict_id,),
            confidence=0.90,
        )
    if p_value >= 0.05:
        return AgentClaim(
            role_id="RESEARCHER",
            stance="CAUTION",
            statement=(
                f"{net:+.2f} net over {trades} trades at {sharpe:.3f} Sharpe per trade, but a "
                f"sign-permutation p of {p_value:.3f} does not separate it from noise."
            ),
            evidence_ids=(verdict.verdict_id,),
            confidence=round(min(0.9, 0.5 + p_value), 4),
        )
    return AgentClaim(
        role_id="RESEARCHER",
        stance="SUPPORT",
        statement=(
            f"{net:+.2f} net over {trades} trades at {sharpe:.3f} Sharpe per trade, with a "
            f"sign-permutation p of {p_value:.3f}."
        ),
        evidence_ids=(verdict.verdict_id,),
        confidence=round(max(0.5, 1.0 - p_value * 10), 4),
    )


def _critic(verdict: Verdict) -> AgentClaim:
    failed = [gate for gate in verdict.gates if gate.status == "FAIL"]
    unmeasured = [gate for gate in verdict.gates if gate.status == "INCONCLUSIVE"]

    if failed:
        worst = failed[0]
        return AgentClaim(
            role_id="CRITIC",
            stance="OPPOSE",
            statement=(
                f"{worst.gate} ({worst.name}) failed: observed {worst.observed} against "
                f"'{worst.rule}'."
                + (f" {len(failed) - 1} further gate(s) also failed." if len(failed) > 1 else "")
            ),
            evidence_ids=(verdict.verdict_id, *(gate.gate for gate in failed)),
            # A failed gate is a measurement, not an opinion.
            confidence=0.95,
        )
    if unmeasured:
        names = ", ".join(gate.gate for gate in unmeasured)
        return AgentClaim(
            role_id="CRITIC",
            stance="CAUTION",
            statement=(
                f"Nothing failed, but {names} were never measured. Absent evidence is not "
                "weak evidence for promotion; it is no evidence."
            ),
            evidence_ids=(verdict.verdict_id, *(gate.gate for gate in unmeasured)),
            confidence=round(min(0.95, 0.55 + 0.1 * len(unmeasured)), 4),
        )
    return AgentClaim(
        role_id="CRITIC",
        stance="SUPPORT",
        statement="Every gate was measured and every gate passed. I have no objection to record.",
        evidence_ids=(verdict.verdict_id,),
        confidence=0.60,
    )


def _risk_auditor(verdict: Verdict) -> AgentClaim:
    drawdown = _metric(verdict, "max_drawdown")
    calmar = _metric(verdict, "calmar")
    trades = int(_metric(verdict, "trades"))
    track_record = _metric(verdict, "minimum_track_record")
    broken = [gate for gate in _statuses(verdict, _RISK_GATES) if gate.status == "FAIL"]

    if broken:
        gate = broken[0]
        return AgentClaim(
            role_id="RISK_AUDITOR",
            stance="OPPOSE",
            statement=(
                f"{gate.gate} ({gate.name}) failed at {gate.observed}. Worst drawdown "
                f"{drawdown:.2f} against a Calmar of {calmar:.2f}."
            ),
            evidence_ids=(verdict.verdict_id, gate.gate),
            confidence=0.92,
        )
    if track_record > 0 and trades < track_record:
        return AgentClaim(
            role_id="RISK_AUDITOR",
            stance="CAUTION",
            statement=(
                f"{trades} trades against a minimum track record of {track_record:.0f} for the "
                "observed Sharpe. The tails are not yet estimable at this sample size."
            ),
            evidence_ids=(verdict.verdict_id, "G3"),
            confidence=0.85,
        )
    return AgentClaim(
        role_id="RISK_AUDITOR",
        stance="CAUTION",
        statement=(
            f"Loss side is within the gate: worst drawdown {drawdown:.2f}, Calmar {calmar:.2f}. "
            "Fills remain modelled rather than calibrated against an execution venue, so the "
            "realised drawdown can only be worse than this."
        ),
        evidence_ids=(verdict.verdict_id, "G8"),
        confidence=0.70,
    )


def _arbiter(verdict: Verdict) -> AgentClaim:
    unmeasured = [gate for gate in verdict.gates if gate.status == "INCONCLUSIVE"]
    statistical = [gate for gate in _statuses(verdict, _STATISTICAL_GATES) if gate.status != "PASS"]

    if verdict.decision == "FAIL":
        return AgentClaim(
            role_id="ARBITER",
            stance="OPPOSE",
            statement=(
                f"Rejected at grade {verdict.grade}. A failed gate is decisive and no amount of "
                "narrative changes it."
            ),
            evidence_ids=(verdict.verdict_id,),
            confidence=0.95,
        )
    if verdict.decision == "INCONCLUSIVE":
        return AgentClaim(
            role_id="ARBITER",
            stance="CAUTION",
            statement=(
                f"Undecided: {len(unmeasured)} gate(s) were never measured"
                + (
                    f", including {len(statistical)} of the selection-integrity set"
                    if statistical
                    else ""
                )
                + ". This is an unanswered question, not a near miss."
            ),
            evidence_ids=(verdict.verdict_id, *(gate.gate for gate in unmeasured)),
            confidence=0.80,
        )
    return AgentClaim(
        role_id="ARBITER",
        stance="SUPPORT",
        statement=(
            f"Every gate measured and passed at grade {verdict.grade}. Qualified as a research "
            "candidate — which is not the same as profitable, and promotion remains human-gated."
        ),
        evidence_ids=(verdict.verdict_id,),
        confidence=0.75,
    )


def build_debate(verdict: Verdict) -> DebateReport:
    """Four specialist positions on one verdict, disagreement intact."""
    claims = (
        _researcher(verdict),
        _critic(verdict),
        _risk_auditor(verdict),
        _arbiter(verdict),
    )
    stances = {claim.stance for claim in claims}
    return DebateReport(
        debate_id=stable_id(
            "debate", {"run_id": verdict.run_id, "verdict_id": verdict.verdict_id}
        ),
        run_id=verdict.run_id,
        roles=_roles(),
        claims=claims,
        # Dissent is any disagreement, not only outright opposition: a panel
        # split between SUPPORT and CAUTION has told the reader something.
        dissent_present=len(stances) > 1,
    )
