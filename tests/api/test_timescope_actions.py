"""Time scopes and plan review through the action registry.

Same reason the campaign verbs exist: the interface could set a campaign's
dates and the assistant had no way to reason about a window at all. These are
the verbs that let one surface propose a window and another check it.
"""

from __future__ import annotations

import pytest
from forge_api.actions import ActionError


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_HOME", str(tmp_path))
    from forge_api.main import create_app

    return create_app()


@pytest.fixture
def actions(app):
    return app.state.actions


WHY = (
    "Recent intraday microstructure: execution conditions and the volatility regime "
    "have both changed since 2020, so older bars describe a different market."
)


def test_the_reservoir_is_reported_as_a_reservoir(actions) -> None:
    payload = actions.call("describe_reservoir", {"dataset": "synthetic"})
    assert payload["years"] > 0
    assert "not the experiment window" in payload["note"]


def test_an_unknown_dataset_is_refused(actions) -> None:
    with pytest.raises(ActionError):
        actions.call("describe_reservoir", {"dataset": "no-such-dataset"})


def test_a_window_reports_the_share_of_history_it_uses(actions) -> None:
    span = actions.call("describe_reservoir", {"dataset": "synthetic"})
    half = span["years"] / 2
    scope = actions.call(
        "propose_time_scope",
        {"dataset": "synthetic", "method": "RECENT_N_YEARS", "years": half, "rationale": WHY},
    )
    assert scope["coverage"] < 0.75
    assert "of the available history" in scope["note"]
    assert scope["rationale"] == WHY


def test_a_window_with_no_stated_reason_is_refused(actions) -> None:
    """Full history so the span check cannot fire first: this is about the reason."""
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {"dataset": "synthetic", "method": "FULL_AVAILABLE_HISTORY", "rationale": "recent"},
        )
    assert "rationale" in str(exc.value).lower()


def test_asking_for_more_history_than_exists_is_refused(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {"dataset": "synthetic", "method": "RECENT_N_YEARS", "years": 500, "rationale": WHY},
        )
    assert "available" in str(exc.value)


def test_an_unknown_selection_method_lists_the_real_ones(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {"dataset": "synthetic", "method": "VIBES", "rationale": WHY},
        )
    assert "RECENT_N_YEARS" in str(exc.value)


def test_a_fixed_range_needs_both_ends(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {"dataset": "synthetic", "method": "FIXED_DATE_RANGE", "rationale": WHY},
        )
    assert "both" in str(exc.value)


def test_a_malformed_date_is_refused_rather_than_guessed_at(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {
                "dataset": "synthetic", "method": "FIXED_DATE_RANGE",
                "start": "last tuesday", "end": "2026-01-01", "rationale": WHY,
            },
        )
    assert "ISO date" in str(exc.value)


def test_full_history_is_available_as_a_named_choice(actions) -> None:
    scope = actions.call(
        "propose_time_scope",
        {
            "dataset": "synthetic", "method": "FULL_AVAILABLE_HISTORY",
            "rationale": (
                "A day-of-week seasonal effect needs many observations of each "
                "weekday, so the whole record is used deliberately."
            ),
        },
    )
    assert scope["coverage"] == pytest.approx(1.0)
    assert scope["method"] == "FULL_AVAILABLE_HISTORY"


def test_a_walk_forward_longer_than_the_archive_is_refused(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call(
            "propose_time_scope",
            {
                "dataset": "synthetic", "method": "ROLLING",
                "train_months": 24, "test_months": 6, "folds": 8, "rationale": WHY,
            },
        )
    assert "available" in str(exc.value)


def test_one_window_adds_no_selection_exposure(actions) -> None:
    assert actions.call("scope_exposure", {"fingerprints": ["a"]})["counts_as_selection"] is False


def test_several_windows_count_as_selection(actions) -> None:
    report = actions.call("scope_exposure", {"fingerprints": ["a", "b", "c", "a"]})
    assert report["windows_tried"] == 3
    assert report["counts_as_selection"] is True
    assert "Multiple-testing" in report["note"]


def test_the_scope_verbs_are_read_only_and_unprotected(actions) -> None:
    schemas = {s["name"]: s for s in actions.schemas()}
    for name in (
        "describe_reservoir", "propose_time_scope", "scope_exposure", "review_research_plan",
    ):
        assert name in schemas
        assert schemas[name]["protected"] is False
        assert schemas[name]["mutating"] is False, f"{name} should propose, not write"


def test_reviewing_a_plan_returns_the_gate_and_the_frozen_hash(actions) -> None:
    span = actions.call("describe_reservoir", {"dataset": "synthetic"})
    scope = actions.call(
        "propose_time_scope",
        {"dataset": "synthetic", "method": "FULL_AVAILABLE_HISTORY", "rationale": WHY},
    )
    plan = {
        "research_question": "Does compressed overnight range precede directional expansion?",
        "hypothesis": "Compressed overnight range precedes directional expansion at the open.",
        "mechanism": (
            "Liquidity provision withdraws into the open so the same flow moves price further."
        ),
        "falsifiable_prediction": (
            "Expectancy after costs is not greater than zero over the window."
        ),
        "invalidation_condition": "Expectancy is negative in two consecutive folds.",
        "dataset": "synthetic",
        "scope": scope,
        "success_criteria": "Positive net expectancy with at least 200 trades.",
        "failure_criteria": "Non-positive net expectancy.",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    result = actions.call("review_research_plan", {"plan": plan})
    assert result["verdict"]["status"].startswith("PLAN_")
    assert result["preregistration"]["scope_fingerprint"] == scope["fingerprint"]
    assert "G1 re-derives" in result["preregistration"]["note"]
    assert span["years"] > 0


def test_a_plan_that_could_not_be_wrong_is_refused_by_the_gate(actions) -> None:
    scope = actions.call(
        "propose_time_scope",
        {"dataset": "synthetic", "method": "FULL_AVAILABLE_HISTORY", "rationale": WHY},
    )
    plan = {
        "research_question": "Is this strategy any good in the current market conditions?",
        "hypothesis": "This construction should perform well across most conditions.",
        "mechanism": "It captures the general tendency of the market to continue moving.",
        "falsifiable_prediction": "The strategy should do well in most market conditions overall.",
        "invalidation_condition": "It stops working.",
        "dataset": "synthetic",
        "scope": scope,
        "success_criteria": "It works.",
        "failure_criteria": "It does not work.",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    result = actions.call("review_research_plan", {"plan": plan})
    assert result["verdict"]["status"] == "PLAN_REJECTED"
    assert any(
        "no result can disagree" in f["summary"] for f in result["verdict"]["findings"]
    )


def test_a_malformed_plan_is_refused_with_the_field_that_was_wrong(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call("review_research_plan", {"plan": {"dataset": "synthetic"}})
    assert "research_question" in str(exc.value) or "Field required" in str(exc.value)
