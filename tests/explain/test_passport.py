"""The passport composes; it never computes, and absence is a section."""

from __future__ import annotations

from forge.explain.passport import (
    SECTION_DEPTH,
    Depth,
    PassportSources,
    SectionKind,
    build,
)
from forge.judge import Judge
from forge.modes.expertise import ExpertiseLevel

from tests.judge.test_judge import judge_input


def sources(**kwargs) -> PassportSources:
    base = dict(strategy_id="s1", name="Gap reversion", stage="VALIDATED")
    base.update(kwargs)
    return PassportSources(**base)


class TestAbsenceIsASection:
    def test_a_strategy_with_nothing_behind_it_still_has_every_section(self) -> None:
        # Omitting a section would make an unvalidated strategy's passport look
        # shorter rather than weaker.
        passport = build(sources())
        kinds = {section.kind for section in passport.sections}
        for required in (
            SectionKind.VERDICT,
            SectionKind.WALK_FORWARD,
            SectionKind.MONTE_CARLO,
            SectionKind.DRAWDOWN,
            SectionKind.RISK,
            SectionKind.DEPLOYMENT,
        ):
            assert required in kinds

    def test_every_unmeasured_section_says_what_would_measure_it(self) -> None:
        passport = build(sources())
        for section in passport.unmeasured:
            assert section.what_would_measure_it, f"{section.kind} is silent about what is missing"

    def test_a_missing_verdict_is_unmeasured_rather_than_a_failure(self) -> None:
        passport = build(sources())
        verdict = next(s for s in passport.sections if s.kind is SectionKind.VERDICT)
        assert not verdict.measured
        assert "run the judge" in verdict.what_would_measure_it


class TestComposition:
    def test_a_verdict_produces_the_gate_ladder_verbatim(self) -> None:
        judged = Judge().evaluate(judge_input())
        passport = build(sources(verdict=judged))
        gates = next(s for s in passport.sections if s.kind is SectionKind.GATES)
        assert len(gates.points) == len(judged.gates)
        for gate in judged.gates:
            assert any(gate.gate in point and gate.status in point for point in gates.points)

    def test_limitations_are_copied_from_the_verdict_not_restated(self) -> None:
        judged = Judge().evaluate(judge_input())
        passport = build(sources(verdict=judged))
        assert passport.limitations == judged.limitations
        assert passport.labels == judged.labels

    def test_metrics_are_taken_from_the_verdict_rather_than_recomputed(self) -> None:
        judged = Judge().evaluate(judge_input())
        passport = build(sources(verdict=judged))
        evidence = [s for s in passport.sections if s.kind is SectionKind.EVIDENCE]
        if evidence:
            for value in evidence[0].metrics:
                assert value.basis == "out-of-sample run"

    def test_the_risk_section_reports_the_permitted_size(self) -> None:
        passport = build(sources(risk_fraction=0.12, permitted_contracts=3))
        risk = next(s for s in passport.sections if s.kind is SectionKind.RISK)
        assert risk.measured
        assert any("3 contract" in point for point in risk.points)


class TestProgressiveDisclosure:
    def test_guided_shows_fewer_sections_than_quant(self) -> None:
        passport = build(sources())
        assert len(passport.at_depth(Depth.GUIDED).sections) < len(
            passport.at_depth(Depth.QUANT).sections
        )

    def test_quant_shows_everything(self) -> None:
        passport = build(sources())
        assert len(passport.at_depth(Depth.QUANT).sections) == len(passport.sections)

    def test_guided_still_shows_the_verdict_and_what_did_not_hold(self) -> None:
        # Simplifying by hiding the warnings is the failure this whole
        # repository is written against.
        assert SECTION_DEPTH[SectionKind.VERDICT] is Depth.GUIDED
        assert SECTION_DEPTH[SectionKind.FAILURES] is Depth.GUIDED
        assert SECTION_DEPTH[SectionKind.RISK] is Depth.GUIDED

    def test_the_statistical_machinery_is_reserved_for_quant(self) -> None:
        for kind in (
            SectionKind.PARAMETER_SURFACE,
            SectionKind.MONTE_CARLO,
            SectionKind.WALK_FORWARD,
            SectionKind.LINEAGE,
        ):
            assert SECTION_DEPTH[kind] is Depth.QUANT

    def test_the_depth_vocabulary_matches_the_expertise_levels(self) -> None:
        # Two ladders with different rungs would mean a Quant operator seeing an
        # Advanced passport.
        assert {d.value for d in Depth} == {level.value for level in ExpertiseLevel}

    def test_every_section_kind_has_a_declared_depth(self) -> None:
        assert set(SECTION_DEPTH) == set(SectionKind)


class TestRendering:
    def test_the_dictionary_form_explains_each_metric(self) -> None:
        passport = build(sources(max_drawdown=1250.0, expected_drawdown_p95=410.0))
        payload = passport.as_dict()
        drawdown = next(s for s in payload["sections"] if s["kind"] == "drawdown")
        assert len(drawdown["metrics"]) == 2
        assert all("explained" in item for item in drawdown["metrics"])

    def test_the_dictionary_form_names_the_unmeasured_sections(self) -> None:
        payload = build(sources()).as_dict()
        assert "verdict" in payload["unmeasured"]
