"""Check my work: concerns about this request, and honesty about what was not checked."""

from __future__ import annotations

from datetime import UTC, datetime

from forge.explain.preview import (
    Availability,
    DataWindow,
    Preview,
    Severity,
    check,
)

START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, tzinfo=UTC)


def complete_preview(**kwargs) -> Preview:
    base = dict(
        strategy_id="s1",
        hypothesis="Overnight gaps in index futures mean-revert within the first hour.",
        mechanism="Liquidity provision by market makers unwinding overnight inventory.",
        falsification="No excess return in the first hour after a gap, net of costs.",
        preregistration_id="pre-1",
        instruments=("MNQ",),
        timeframe="1m",
        trial_count=40,
        validation_methods=("walk_forward", "cpcv"),
        evidence_tier="TRUTH_OOS",
        cost_model="per-contract commission plus one tick",
        data=(
            DataWindow(
                symbol="MNQ",
                timeframe="1m",
                requested_start=START,
                requested_end=END,
                available_start=START,
                available_end=END,
                rows=500_000,
                availability=Availability.AVAILABLE,
            ),
        ),
    )
    base.update(kwargs)
    return Preview(**base)


class TestDataAvailability:
    def test_missing_data_is_blocking(self) -> None:
        window = DataWindow(symbol="ES", timeframe="1m", availability=Availability.MISSING)
        result = check(complete_preview(data=(window,)))
        assert any(c.severity is Severity.BLOCKING for c in result.concerns)

    def test_a_partial_archive_is_blocking_rather_than_silently_narrowing(self) -> None:
        # §31's real risk: the run tests a shorter window than was asked for and
        # nobody is told.
        window = DataWindow(
            symbol="MNQ",
            timeframe="1m",
            requested_start=START,
            requested_end=END,
            available_start=datetime(2025, 6, 1, tzinfo=UTC),
            available_end=END,
            availability=Availability.AVAILABLE,
        )
        result = check(complete_preview(data=(window,)))
        blocking = [c for c in result.concerns if c.severity is Severity.BLOCKING]
        assert blocking
        assert "silently test a shorter window" in blocking[0].finding

    def test_degraded_data_weakens_rather_than_blocks(self) -> None:
        window = DataWindow(
            symbol="MNQ",
            timeframe="1m",
            requested_start=START,
            requested_end=END,
            available_start=START,
            available_end=END,
            availability=Availability.DEGRADED,
            findings=("14 gaps longer than one session",),
        )
        result = check(complete_preview(data=(window,)))
        concern = next(c for c in result.concerns if c.subject == "MNQ 1m")
        assert concern.severity is Severity.WEAKENS
        assert "14 gaps" in concern.finding

    def test_unchecked_availability_is_reported_as_unchecked_not_as_available(self) -> None:
        window = DataWindow(symbol="MNQ", timeframe="1m")
        result = check(complete_preview(data=(window,)))
        assert any("availability was not checked" in item for item in result.not_checked)
        assert not any(c.subject == "MNQ 1m" for c in result.concerns)

    def test_no_data_windows_at_all_is_reported(self) -> None:
        result = check(complete_preview(data=()))
        assert any("no data windows were resolved" in item for item in result.not_checked)

    def test_covers_request_is_none_rather_than_false_when_unknown(self) -> None:
        assert DataWindow(symbol="MNQ", timeframe="1m").covers_request is None


class TestMethodConcerns:
    def test_a_missing_hypothesis_is_flagged_against_gate_g1(self) -> None:
        result = check(complete_preview(hypothesis="", preregistration_id=""))
        concern = next(c for c in result.concerns if c.subject == "Preregistration")
        assert "G1" in concern.finding

    def test_a_hypothesis_with_no_falsification_is_flagged(self) -> None:
        result = check(complete_preview(falsification=""))
        assert any(c.subject == "Falsification" for c in result.concerns)

    def test_too_few_configurations_is_flagged_against_gate_g11(self) -> None:
        result = check(complete_preview(trial_count=4))
        concern = next(c for c in result.concerns if c.subject == "Selection integrity")
        assert "G11" in concern.finding

    def test_a_single_trial_is_not_flagged_for_cscv(self) -> None:
        # One configuration is a deliberate single run, not a thin sweep.
        result = check(complete_preview(trial_count=1))
        assert not any(c.subject == "Selection integrity" for c in result.concerns)

    def test_no_validation_method_is_flagged_against_g12_and_g13(self) -> None:
        result = check(complete_preview(validation_methods=()))
        concern = next(c for c in result.concerns if c.subject == "Validation")
        assert "G12" in concern.finding and "G13" in concern.finding

    def test_a_sweep_is_noted_as_unable_to_satisfy_g10(self) -> None:
        result = check(complete_preview(evidence_tier="SWEEP"))
        concern = next(c for c in result.concerns if c.subject == "Evidence tier")
        assert concern.severity is Severity.NOTE
        assert "G10" in concern.finding

    def test_no_cost_model_is_flagged_as_an_upper_bound(self) -> None:
        result = check(complete_preview(cost_model=""))
        concern = next(c for c in result.concerns if c.subject == "Costs")
        assert "upper bound" in concern.finding

    def test_no_instrument_is_blocking(self) -> None:
        result = check(complete_preview(instruments=()))
        assert any(
            c.subject == "Instruments" and c.severity is Severity.BLOCKING
            for c in result.concerns
        )


class TestHonesty:
    def test_a_complete_preview_still_reports_what_cannot_be_known_yet(self) -> None:
        # An empty concern list must not read as a clean bill of health.
        result = check(complete_preview())
        assert result.concerns == ()
        assert any("trade count" in item for item in result.not_checked)

    def test_no_concern_suggests_weakening_a_standard(self) -> None:
        # The obvious "fix" for a selection-integrity concern is to declare
        # fewer trials. Nothing here may say so.
        previews = [
            complete_preview(),
            complete_preview(trial_count=4),
            complete_preview(hypothesis="", preregistration_id=""),
            complete_preview(validation_methods=()),
            complete_preview(cost_model=""),
            complete_preview(evidence_tier="SWEEP"),
        ]
        for preview in previews:
            for concern in check(preview).concerns:
                text = f"{concern.finding} {concern.suggestion}".lower()
                for banned in (
                    "lower the threshold",
                    "fewer trials",
                    "reduce the trial count",
                    "relax the",
                    "skip validation",
                    "disable the gate",
                ):
                    assert banned not in text, f"{concern.subject} suggests: {banned}"

    def test_the_dictionary_form_counts_the_blocking_concerns(self) -> None:
        payload = check(complete_preview(instruments=())).as_dict()
        assert payload["blocking"] >= 1
        assert payload["not_checked"]
