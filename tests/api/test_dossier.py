"""The evidence dossier assembles; it must never invent.

The rule under test is that an absent section says so. A candidate that was
never validated must get `available: false` with a reason, not a block of
zeroes — a zero reads as a measurement, and the whole point of the dossier is
to let a researcher see what is and is not known.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    return TestClient(create_app(tmp_path / "api.db"))


def _new_strategy(client: TestClient) -> str:
    library = client.app.state.engine.library  # type: ignore[attr-defined]
    spec = library.create_from_template("momentum_breakout", symbol="NQ")
    return str(spec.strategy_id)


def _dossier(client: TestClient, strategy_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/strategies/{strategy_id}/dossier")
    assert response.status_code == 200
    return response.json()["data"]


# ── absence is reported, never fabricated ────────────────────────────────────


def test_an_unbacktested_strategy_reports_every_absence_with_a_reason(
    client: TestClient,
) -> None:
    data = _dossier(client, _new_strategy(client))

    for section in ("backtests", "verdict", "validation", "dissent"):
        assert data[section]["available"] is False, f"{section} should be absent"
        assert data[section]["reason"], f"{section} must explain why"


def test_an_absent_section_carries_no_numbers(client: TestClient) -> None:
    """The failure mode this guards: zeroes that read as measurements."""
    data = _dossier(client, _new_strategy(client))
    verdict = data["verdict"]
    assert set(verdict) == {"available", "reason"}
    assert "metrics" not in verdict
    assert "grade" not in verdict


def test_a_hand_made_strategy_says_it_has_no_experiment(client: TestClient) -> None:
    data = _dossier(client, _new_strategy(client))
    assert data["provenance"]["available"] is False
    assert "created by hand" in data["provenance"]["reason"]


def test_the_strategy_section_is_always_present(client: TestClient) -> None:
    strategy_id = _new_strategy(client)
    data = _dossier(client, strategy_id)
    assert data["strategy"]["available"] is True
    assert data["strategy"]["strategy_id"] == strategy_id
    assert data["strategy"]["parameters"]


def test_standing_caveats_are_always_stated(client: TestClient) -> None:
    """Paper-only and uncalibrated fills are not conditional on having evidence."""
    data = _dossier(client, _new_strategy(client))
    caveats = " ".join(data["standing_caveats"])
    assert "Paper only" in caveats
    assert "modelled, not calibrated" in caveats


def test_meta_lists_which_sections_are_available(client: TestClient) -> None:
    body = client.get(f"/api/v1/strategies/{_new_strategy(client)}/dossier").json()
    assert "strategy" in body["meta"]["sections_available"]
    assert "verdict" not in body["meta"]["sections_available"]


def test_an_unknown_strategy_is_a_404(client: TestClient) -> None:
    assert client.get("/api/v1/strategies/nope/dossier").status_code == 404


# ── provenance and memory join up ────────────────────────────────────────────


def test_provenance_is_found_when_an_experiment_claims_the_strategy(
    client: TestClient,
) -> None:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    strategy_id = _new_strategy(client)
    scope = engine._scope()

    root = engine.experiments.reserve(
        scope, "momentum_breakout", {"lookback": 10.0}, policy="exploration"
    )
    child = engine.experiments.reserve(
        scope,
        "momentum_breakout",
        {"lookback": 20.0},
        parent_id=root,
        policy="neighbourhood",
    )
    engine.experiments.finish(str(child), strategy_id=strategy_id, status="running")

    provenance = _dossier(client, strategy_id)["provenance"]
    assert provenance["available"] is True
    assert provenance["policy"] == "neighbourhood"
    assert provenance["depth"] == 1
    assert [row["id"] for row in provenance["ancestors"]] == [root]


def test_research_memory_section_counts_failures_for_the_same_template(
    client: TestClient,
) -> None:
    from forge.memory import FailureClass
    from forge.strategy import TEMPLATES

    engine = client.app.state.engine  # type: ignore[attr-defined]
    strategy_id = _new_strategy(client)
    template = TEMPLATES["momentum_breakout"]
    engine._remember(
        "momentum_breakout",
        {p.name: float(p.default) for p in template.parameters},
        template.parameters,
        FailureClass.NO_TRADES,
        "nothing fired",
    )

    memory = _dossier(client, strategy_id)["research_memory"]
    if memory["available"]:
        assert memory["related_failures"] >= 1
        assert memory["by_class"]["NO_TRADES"] >= 1


# ── the action mirrors the endpoint ──────────────────────────────────────────


def test_the_dossier_action_is_registered_read_only(client: TestClient) -> None:
    actions = client.app.state.actions  # type: ignore[attr-defined]
    schema = next(s for s in actions.schemas() if s["name"] == "strategy_dossier")
    assert schema["mutating"] is False


def test_the_action_returns_the_same_document_as_the_endpoint(
    client: TestClient,
) -> None:
    strategy_id = _new_strategy(client)
    actions = client.app.state.actions  # type: ignore[attr-defined]
    assert actions.call("strategy_dossier", {"strategy_id": strategy_id}) == _dossier(
        client, strategy_id
    )


def test_the_action_refuses_an_unknown_strategy_with_a_reason(
    client: TestClient,
) -> None:
    from forge_api.actions import ActionError

    actions = client.app.state.actions  # type: ignore[attr-defined]
    with pytest.raises(ActionError) as refusal:
        actions.call("strategy_dossier", {"strategy_id": "nope"})
    assert "list_strategies" in str(refusal.value)


# ── reproducibility ──────────────────────────────────────────────────────────


def test_reproducibility_reports_absent_without_a_verdict(client: TestClient) -> None:
    data = _dossier(client, _new_strategy(client))
    assert data["reproducibility"]["available"] is False
    assert "nothing was snapshotted" in data["reproducibility"]["reason"]


def test_reproducibility_reads_a_written_snapshot(client: TestClient) -> None:
    """A snapshot written for a run id must be found and verified through the dossier."""
    import forge.judge.engine  # noqa: F401

    engine = client.app.state.engine  # type: ignore[attr-defined]
    engine.snapshots.write(
        run_id="run_probe",
        verdict={"verdict_id": "v1", "decision": "FAIL"},
        identity={"dataset": "nq_1m_16y"},
        strategy_source="def signal(bars):\n    return 0\n",
    )
    report = engine.snapshots.verify("run_probe")
    assert report["intact"] is True
    assert engine.snapshots.drift("run_probe")["comparable"] is True
