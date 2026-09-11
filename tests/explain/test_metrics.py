"""The glossary: tied to the code that computes the metrics, and to its thresholds."""

from __future__ import annotations

import importlib

import pytest
from forge.explain.metrics import METRICS, Reading, catalogue, interpret, metric


class TestTheGlossaryIsTiedToTheCode:
    def test_every_metric_names_a_function_that_exists(self) -> None:
        # A glossary that drifts from the code is worse than no glossary,
        # because it is believed.
        for entry in METRICS.values():
            if not entry.computed_by:
                continue
            module_path, _, name = entry.computed_by.rpartition(".")
            module = importlib.import_module(module_path)
            assert hasattr(module, name), f"{entry.computed_by} does not exist"

    def test_thresholds_are_read_from_the_module_that_enforces_them(self) -> None:
        from forge.judge.engine import DEFLATED_SHARPE_THRESHOLD, OVERFITTING_THRESHOLD

        assert METRICS["deflated_sharpe"].threshold == DEFLATED_SHARPE_THRESHOLD
        assert METRICS["pbo"].threshold == OVERFITTING_THRESHOLD

    def test_every_metric_with_a_threshold_says_where_it_is_set(self) -> None:
        for entry in METRICS.values():
            if entry.threshold is not None:
                assert entry.threshold_source, f"{entry.key} has a bar but does not say whose"

    def test_every_metric_answers_all_four_questions(self) -> None:
        for entry in METRICS.values():
            assert entry.what and entry.why and entry.definition
            assert entry.caveats, f"{entry.key} claims to have no caveats"


class TestInterpretation:
    def test_an_unmeasured_value_is_not_comparable_rather_than_zero(self) -> None:
        result = interpret("calmar", None)
        assert result.reading is Reading.NOT_COMPARABLE
        assert "was not measured" in result.sentence

    def test_a_metric_with_no_bar_reports_no_standard(self) -> None:
        # So the interface does not draw a verdict pill against a number nobody
        # set a bar for.
        assert interpret("sharpe", 1.4).reading is Reading.NO_STANDARD

    def test_a_lower_is_better_metric_compares_the_right_way(self) -> None:
        assert interpret("pbo", 0.1).reading is Reading.MEETS
        assert interpret("pbo", 0.9).reading is Reading.BELOW

    def test_a_higher_is_better_metric_compares_the_right_way(self) -> None:
        assert interpret("profit_factor", 1.5).reading is Reading.MEETS
        assert interpret("profit_factor", 1.0).reading is Reading.BELOW

    def test_the_sentence_names_the_bar_and_who_set_it(self) -> None:
        sentence = interpret("deflated_sharpe", 0.2).sentence
        assert "forge.judge.engine.DEFLATED_SHARPE_THRESHOLD" in sentence
        assert "G5" in sentence

    def test_caller_context_is_appended_verbatim_and_never_invented(self) -> None:
        plain = interpret("deflated_sharpe", 0.2)
        with_context = interpret("deflated_sharpe", 0.2, context="Best-of-142 hurdle: 0.33.")
        assert with_context.sentence == plain.sentence + " Best-of-142 hurdle: 0.33."

    def test_an_unknown_metric_raises_with_the_known_set(self) -> None:
        with pytest.raises(KeyError, match="sharpe"):
            interpret("made_up", 1.0)

    def test_interpretation_carries_the_caveats(self) -> None:
        assert interpret("max_drawdown", 500.0).caveats


class TestCatalogue:
    def test_the_whole_glossary_renders(self) -> None:
        assert len(catalogue()) == len(METRICS)

    def test_a_subset_renders_in_the_order_asked_for(self) -> None:
        assert [item["key"] for item in catalogue(("pbo", "sharpe"))] == ["pbo", "sharpe"]

    def test_an_unknown_key_is_skipped_rather_than_invented(self) -> None:
        assert catalogue(("nope",)) == []

    def test_metric_returns_none_for_an_unknown_key(self) -> None:
        assert metric("nope") is None
        assert metric("sharpe") is not None


class TestTheNumbersMatterHere:
    def test_the_prop_desk_metrics_are_in_the_glossary(self) -> None:
        # The two numbers the risk screens show most are the two most likely to
        # be misread, so they are explained beside the judge's own.
        assert "expected_drawdown_p95" in METRICS
        assert "risk_fraction" in METRICS

    def test_the_risk_fraction_entry_says_it_is_a_share_of_buffer_not_equity(self) -> None:
        caveats = " ".join(METRICS["risk_fraction"].caveats)
        assert "not of equity" in caveats
