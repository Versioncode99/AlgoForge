"""Fourteen threats, answered one at a time, deciding nothing.

§6's requirement is not "an adversarial agent" -- it is that the adversarial
output "must not simply be 'another opinion'" and "must produce structured
objections", against fourteen named threats, followed by a synthesis over seven
named fields. What makes that a schema rather than a summary is the row for the
threat nobody could check.
"""

from __future__ import annotations

import pytest
from forge.agents import build_debate
from forge.agents.objections import (
    MEANS,
    SETTLED_BY,
    Threat,
    object_to,
    synthesise,
)
from forge.judge.models import CalculationTrace, GateResult, Verdict


def gate(name: str, status: str, label: str = "") -> GateResult:
    return GateResult(
        gate=name,
        name=label or name,
        status=status,  # type: ignore[arg-type]
        observed="observed",
        rule="the rule",
        finding="the finding",
    )


def verdict(*gates: GateResult, decision: str = "FAIL") -> Verdict:
    return Verdict(
        verdict_id="v1",
        run_id="r1",
        decision=decision,  # type: ignore[arg-type]
        grade="C",
        gates=gates,
        dimensions={"a": 1, "b": 1, "c": 1, "d": 1},
        metrics={"net_pnl": 1.0, "trades": 40, "sharpe_per_trade": 0.1},
        traces=(
            CalculationTrace(
                trace_id="t1",
                metric="net_pnl",
                formula="sum(net)",
                inputs={},
                value=1.0,
                source_tier="SAMPLE",
            ),
        ),
    )


def test_all_fourteen_threats_are_the_ones_the_directive_names() -> None:
    assert [str(threat) for threat in Threat] == [
        "SELECTION_BIAS",
        "MULTIPLE_TESTING",
        "LEAKAGE",
        "REGIME_DEPENDENCE",
        "INSUFFICIENT_SAMPLE",
        "DATA_MINING",
        "ALTERNATIVE_EXPLANATION",
        "EXECUTION_COSTS",
        "INSTABILITY",
        "TEMPORAL_INSTABILITY",
        "SURVIVORSHIP",
        "OVERFITTING",
        "CONSTRUCTION_DUPLICATION",
        "BENCHMARK_DEPENDENCE",
    ]


def test_every_threat_has_a_meaning_and_a_way_it_could_be_settled() -> None:
    """A threat with neither is a word on a list rather than a check."""
    for threat in Threat:
        assert MEANS[threat], f"{threat} does not say what the failure is"
        gate_name, fallback = SETTLED_BY[threat]
        assert gate_name or fallback, f"{threat} names neither a gate nor an experiment"


def test_a_verdict_with_no_gates_still_answers_all_fourteen() -> None:
    """The whole point. An absent row cannot say "nobody looked"."""
    found = object_to(verdict())
    assert len(found) == len(Threat)
    assert {item.threat for item in found} == set(Threat)
    assert all(item.finding == "NOT_MEASURED" for item in found)
    assert all(item.required_experiment for item in found)


def test_a_failed_gate_raises_its_threat_and_names_it() -> None:
    found = {item.threat: item for item in object_to(verdict(gate("G2", "FAIL", "Implementation")))}
    leakage = found[Threat.LEAKAGE]
    assert leakage.finding == "RAISED"
    assert leakage.gate == "G2"
    assert "Implementation" in leakage.statement
    assert leakage.required_experiment == "", "a raised threat needs no experiment to settle it"


def test_a_passing_gate_clears_its_threat() -> None:
    judged = verdict(gate("G3", "PASS", "Sample adequacy"))
    found = {item.threat: item for item in object_to(judged)}
    sample = found[Threat.INSUFFICIENT_SAMPLE]
    assert sample.finding == "CLEARED"
    assert sample.gate == "G3"
    assert "Sample adequacy" in sample.statement


def test_an_inconclusive_gate_leaves_its_threat_unanswered() -> None:
    """Not cleared. Absent evidence is not weak evidence in favour."""
    found = {item.threat: item for item in object_to(verdict(gate("G5", "INCONCLUSIVE")))}
    multiple = found[Threat.MULTIPLE_TESTING]
    assert multiple.finding == "NOT_MEASURED"
    assert multiple.required_experiment


def test_the_four_threats_no_gate_settles_say_what_would() -> None:
    """Survivorship, duplication, benchmark and venue-calibrated costs.

    Each is honestly outside this ladder, and an answer derived from a gate
    measuring something else would be worse than none.
    """
    every = {name: gate(name, "PASS") for name in (f"G{n}" for n in range(14))}
    found = {item.threat: item for item in object_to(verdict(*every.values(), decision="PASS"))}
    for threat in (
        Threat.SURVIVORSHIP,
        Threat.CONSTRUCTION_DUPLICATION,
        Threat.BENCHMARK_DEPENDENCE,
    ):
        assert found[threat].finding == "NOT_MEASURED"
        assert found[threat].gate == ""
        assert len(found[threat].required_experiment) > 30

    # Execution costs *has* a gate and the caveat still travels with it: G7
    # checks two engines agree, which is not either being right about a venue.
    costs = found[Threat.EXECUTION_COSTS]
    assert costs.finding == "CLEARED"
    assert "venue-calibrated" in costs.statement


def test_one_threat_per_row_and_no_duplicates() -> None:
    found = object_to(verdict(gate("G5", "PASS")))
    assert len({item.threat for item in found}) == len(found)


# ── the synthesis ─────────────────────────────────────────────────────────────


def test_the_synthesis_carries_the_seven_fields_the_directive_names() -> None:
    report = build_debate(verdict(gate("G2", "FAIL"), gate("G3", "PASS")))
    assert report.synthesis is not None
    for field in (
        "supported",
        "unsupported",
        "contradictions",
        "unresolved",
        "required_experiments",
        "confidence",
        "evidence_ids",
    ):
        assert hasattr(report.synthesis, field), f"{field} is missing from the synthesis"


def test_supported_and_unsupported_are_the_cleared_and_the_raised() -> None:
    objections = object_to(verdict(gate("G2", "FAIL"), gate("G3", "PASS")))
    synthesis = synthesise(verdict(gate("G2", "FAIL"), gate("G3", "PASS")), objections, ())
    assert any("LEAKAGE" in line for line in synthesis.unsupported)
    assert any("INSUFFICIENT_SAMPLE" in line for line in synthesis.supported)


def test_confidence_is_the_share_of_threats_a_gate_actually_settled() -> None:
    """Not a probability that the strategy makes money, and nothing reads it."""
    nothing = synthesise(verdict(), object_to(verdict()), ())
    assert nothing.confidence == 0.0

    one = verdict(gate("G3", "PASS"))
    assert synthesise(one, object_to(one), ()).confidence == pytest.approx(1 / 14, abs=1e-4)


def test_a_pass_with_a_raised_threat_is_a_contradiction_worth_surfacing() -> None:
    passing = verdict(gate("G2", "FAIL"), decision="PASS")
    synthesis = synthesise(passing, object_to(passing), ())
    assert any("PASS while" in line for line in synthesis.contradictions)


def test_a_split_panel_is_recorded_as_a_contradiction() -> None:
    from forge.agents.models import AgentClaim

    claims = (
        AgentClaim(role_id="RESEARCHER", stance="SUPPORT", statement="s", evidence_ids=("v1",),
                   confidence=0.6),
        AgentClaim(role_id="CRITIC", stance="OPPOSE", statement="o", evidence_ids=("v1",),
                   confidence=0.9),
    )
    synthesis = synthesise(verdict(), object_to(verdict()), claims)
    assert any("RESEARCHER" in line and "CRITIC" in line for line in synthesis.contradictions)


def test_required_experiments_do_not_repeat_themselves() -> None:
    """Two threats settled by the same absent gate name it once."""
    synthesis = synthesise(verdict(), object_to(verdict()), ())
    assert len(synthesis.required_experiments) == len(set(synthesis.required_experiments))


def test_none_of_this_can_move_the_verdict() -> None:
    """The deterministic validation engine remains authoritative, asserted.

    Built twice from the same verdict, and the verdict object is compared with
    itself before and after: an objection that could change a number would make
    the ladder stop being the judge.
    """
    subject = verdict(gate("G2", "FAIL"), gate("G5", "PASS"))
    before = subject.model_dump(mode="json")
    report = build_debate(subject)
    assert subject.model_dump(mode="json") == before
    assert report.numeric_verdict_locked is True
    assert report.synthesis is not None
    assert report.synthesis.numeric_verdict_locked is True


def test_the_report_is_deterministic() -> None:
    subject = verdict(gate("G2", "FAIL"), gate("G5", "PASS"))
    assert build_debate(subject) == build_debate(subject)
