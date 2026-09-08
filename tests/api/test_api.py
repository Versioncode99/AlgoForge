from __future__ import annotations

from fastapi.testclient import TestClient
from forge_api.main import create_app


def test_health_capabilities_and_seeded_run(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "api.db")) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["data"]["paper_only"] is True

        capabilities = client.get("/api/v1/capabilities").json()["data"]["capabilities"]
        assert capabilities["live_order"] is False

        runs = client.get("/api/v1/runs").json()
        assert runs["meta"]["total"] == 1
        assert "SAMPLE_DATA" in runs["data"][0]["labels"]
        run_id = runs["data"][0]["run_id"]
        # Three routes on this app used to answer for a bare run record by
        # inventing the trades the answer needed and then running them through
        # the real machinery — the judge, the specialist debate, the prop
        # simulator. Each returned a well-formed, fully-populated result. All
        # three now refuse and name the route that reads an actual ledger.
        verdict = client.get(f"/api/v1/verdicts/{run_id}")
        assert verdict.status_code == 409
        assert verdict.json()["detail"]["code"] == "no_judgeable_evidence"
        assert any("dossier" in route for route in verdict.json()["detail"]["judge_instead"])
        # This route used to answer with a hard-coded P&L series run through the
        # judge, so every number in it was invented and the verdict attached to
        # them was real. A run record carries provenance and no P&L, so the only
        # honest answer is a refusal that names where the real analysis lives.
        analysis = client.get(f"/api/v1/analysis/{run_id}")
        assert analysis.status_code == 409
        detail = analysis.json()["detail"]
        assert detail["code"] == "no_analysable_evidence"
        assert any("/trades" in route for route in detail["analyse_instead"])
        rules = client.get("/api/v1/prop/rules").json()
        assert rules["meta"]["runnable"] == 0
        rule_id = rules["data"][0]["rule_id"]
        # The seeded run is SWEEP tier. A sweep is exploration, not out-of-sample
        # evidence, so the prop simulator must refuse it outright rather than
        # returning a pass rate that would read as a result. The tier check runs
        # first, so it is the tier that is named.
        prop = client.get(f"/api/v1/prop/simulations/{run_id}", params={"rule_id": rule_id})
        assert prop.status_code == 422
        assert prop.json()["detail"]["code"] == "truth_or_forward_required"
        agents = client.get(f"/api/v1/agents/{run_id}")
        assert agents.status_code == 409
        assert agents.json()["detail"]["code"] == "no_verdict_to_review"
        evolution = client.get("/api/v1/evolution/overview").json()
        assert evolution["data"]["automatic_live_changes"] is False
        assert evolution["data"]["candidate"]["lane"] == "CLEAN_ROOM"


def test_missing_run_returns_semantic_404(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "api.db")) as client:
        response = client.get("/api/v1/runs/run_missing")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "run_not_found"


def test_a_truth_tier_run_still_gets_no_invented_prop_simulation(tmp_path) -> None:
    """The tier gate is not what makes the old route safe — the refusal is.

    `/prop/simulations/{run_id}` used to reject SWEEP-tier runs and then answer
    for anything else by drawing ninety daily P&L figures from a seeded normal
    distribution. A test that only ever exercised the SWEEP path would have gone
    on passing while the route returned a fabricated pass rate for every
    out-of-sample run in the ledger.
    """
    from forge.contracts.models import RunRecord

    with TestClient(create_app(tmp_path / "api.db")) as client:
        seed = client.get("/api/v1/runs").json()["data"][0]
        truth = RunRecord(**{**seed, "run_id": "run_truth_tier", "tier": "TRUTH_OOS"})
        client.app.state.ledger.add_run(truth)  # type: ignore[attr-defined]

        rule_id = client.get("/api/v1/prop/rules").json()["data"][0]["rule_id"]
        response = client.get(
            "/api/v1/prop/simulations/run_truth_tier", params={"rule_id": rule_id}
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "no_trades_to_simulate"
        assert any("/prop" in route for route in detail["simulate_instead"])
