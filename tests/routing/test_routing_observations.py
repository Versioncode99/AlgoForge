"""Routing that can be learned from, and cannot quietly learn.

D1 §24 in three refusals: nothing here changes a setting, a handful of
observations recommends nothing, and what is unknown stays empty rather than
being defaulted into the arithmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge_api.routing_observations import (
    MATERIAL_MARGIN,
    MINIMUM_OBSERVATIONS,
    RoutingObservations,
    recommend,
)


@pytest.fixture
def store(tmp_path: Path) -> RoutingObservations:
    return RoutingObservations(tmp_path / "routing.db")


def _fill(
    store: RoutingObservations, role: str, model: str, *, ok: int, failed: int = 0
) -> None:
    for _ in range(ok):
        store.record(
            task="propose", role=role, model=model, provider="p", result="ok", latency_ms=900
        )
    for _ in range(failed):
        store.record(
            task="propose", role=role, model=model, provider="p", result="failed", latency_ms=1500
        )


def test_every_field_the_directive_names_is_recorded(store: RoutingObservations) -> None:
    store.record(
        task="propose a breakout",
        role="hypothesis",
        model="m1",
        provider="openai",
        result="ok",
        latency_ms=1234,
    )
    row = store.rows()[0]
    for field in ("task", "role", "model", "provider", "result", "latency_ms"):
        assert row[field], f"{field} was not recorded"
    assert row["latency_ms"] == 1234


def test_what_is_not_known_yet_is_empty_rather_than_neutral(store: RoutingObservations) -> None:
    """A default would put a number nobody measured into the recommendation."""
    store.record(
        task="t", role="r", model="m", provider="p", result="ok", latency_ms=10
    )
    row = store.rows()[0]
    assert row["quality"] == ""
    assert row["downstream"] == ""


def test_an_outcome_can_be_attached_later_and_never_overwrites(
    store: RoutingObservations,
) -> None:
    observation_id = store.record(
        task="t", role="r", model="m", provider="p", result="ok", latency_ms=10
    )
    assert store.attach_outcome(observation_id, downstream="experiment_ran", quality="good")
    assert store.rows()[0]["downstream"] == "experiment_ran"

    store.attach_outcome(observation_id, downstream="something_else", quality="bad")
    assert store.rows()[0]["downstream"] == "experiment_ran", "a known outcome was overwritten"
    assert store.rows()[0]["quality"] == "good"


def test_attaching_to_nothing_is_false_rather_than_an_error(
    store: RoutingObservations,
) -> None:
    assert store.attach_outcome("obs_nope", downstream="x") is False


def test_the_summary_counts_each_outcome_separately(store: RoutingObservations) -> None:
    _fill(store, "hypothesis", "m1", ok=6, failed=4)
    row = next(item for item in store.summary() if item["model"] == "m1")
    assert row["calls"] == 10
    assert row["ok"] == 6
    assert row["failed"] == 4
    assert row["success_rate"] == pytest.approx(0.6)


# ── what it recommends, and what it refuses to ────────────────────────────────


def test_a_handful_of_observations_recommends_nothing(store: RoutingObservations) -> None:
    """The directive's own words, made into a threshold with a number attached."""
    _fill(store, "hypothesis", "m1", ok=3)
    found = recommend(store.summary(), {"hypothesis": "m1"})
    assert len(found) == 1
    assert found[0].actionable is False
    assert found[0].suggested == "m1"
    assert f"{MINIMUM_OBSERVATIONS}" in found[0].reason
    assert "more before" in found[0].reason


def test_thin_evidence_still_produces_a_row(store: RoutingObservations) -> None:
    """An absent row reads as "nothing to see", which is a different claim."""
    found = recommend([], {"hypothesis": "m1", "regime": "m2"})
    assert {item.role for item in found} == {"hypothesis", "regime"}
    assert all(item.actionable is False for item in found)


def test_a_clearly_better_model_is_recommended_and_not_applied(
    store: RoutingObservations,
) -> None:
    _fill(store, "hypothesis", "m1", ok=10, failed=20)
    _fill(store, "hypothesis", "m2", ok=27, failed=3)
    assigned = {"hypothesis": "m1"}
    found = recommend(store.summary(), assigned)

    assert found[0].actionable is True
    assert found[0].suggested == "m2"
    assert "nothing here changes it for you" in found[0].reason
    # The assignment it was given is untouched: the function returns advice.
    assert assigned == {"hypothesis": "m1"}


def test_a_small_difference_is_not_worth_changing_a_setting_for(
    store: RoutingObservations,
) -> None:
    _fill(store, "hypothesis", "m1", ok=20, failed=10)
    # Just inside the margin: better, and not by enough to be worth the churn.
    _fill(store, "hypothesis", "m2", ok=21, failed=9)
    found = recommend(store.summary(), {"hypothesis": "m1"})
    assert found[0].actionable is False
    assert found[0].suggested == "m1"
    assert f"{MATERIAL_MARGIN:.0%}" in found[0].reason


def test_a_challenger_with_too_few_calls_cannot_win(store: RoutingObservations) -> None:
    """Three lucky calls must not unseat a model with thirty."""
    _fill(store, "hypothesis", "m1", ok=15, failed=15)
    _fill(store, "hypothesis", "m2", ok=3)
    found = recommend(store.summary(), {"hypothesis": "m1"})
    assert found[0].actionable is False
    assert found[0].suggested == "m1"
    assert "nothing to compare against" in found[0].reason


def test_a_role_with_no_assigned_model_is_left_alone(store: RoutingObservations) -> None:
    _fill(store, "hypothesis", "m1", ok=30)
    assert recommend(store.summary(), {"hypothesis": ""}) == []


def test_observations_for_one_role_do_not_decide_another(store: RoutingObservations) -> None:
    _fill(store, "hypothesis", "m2", ok=30)
    _fill(store, "regime", "m1", ok=30)
    found = {item.role: item for item in recommend(store.summary(), {"regime": "m1"})}
    assert found["regime"].suggested == "m1"
    assert "nothing to compare against" in found["regime"].reason


# ── over HTTP ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from fastapi.testclient import TestClient
    from forge_api.main import create_app

    with TestClient(create_app(database_path=tmp_path / "routing.db")) as client:
        yield client


def test_no_route_can_post_an_observation(client) -> None:
    """An observation is caused by a call. A route that could write one would
    let the evidence say a model did work it never did."""
    for method, path in (
        ("POST", "/api/v1/routing/observations"),
        ("PUT", "/api/v1/routing/observations"),
        ("POST", "/api/v1/routing/recommendations"),
    ):
        response = client.request(method, path, json={"model": "invented", "result": "ok"})
        assert response.status_code in (404, 405), f"{method} {path} was accepted"


def test_the_recommendation_route_says_it_applies_nothing(client) -> None:
    body = client.get("/api/v1/routing/recommendations").json()
    assert "Nothing here changes routing" in body["meta"]["note"]
    assert body["meta"]["minimum_observations"] == MINIMUM_OBSERVATIONS
    # Every routed role gets a row, even with no evidence at all.
    assert body["data"]["recommendations"] or body["data"]["actionable"] == 0


def test_the_observations_route_names_the_fields_the_directive_asked_for(client) -> None:
    body = client.get("/api/v1/routing/observations").json()
    assert set(body["data"]["fields"]) >= {
        "task", "role", "model", "provider", "result", "latency_ms", "quality", "downstream",
    }
    assert "not known" in body["meta"]["note"]
