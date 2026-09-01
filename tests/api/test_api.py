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
        verdict = client.get(f"/api/v1/verdicts/{run_id}").json()["data"]
        assert verdict["decision"] == "PASS"
        assert len(verdict["traces"]) == len(verdict["metrics"])
        analysis = client.get(f"/api/v1/analysis/{run_id}").json()["data"]
        assert analysis["risk"]["path_count"] == 240
        assert len(analysis["regimes"]) == 4
        rules = client.get("/api/v1/prop/rules").json()
        assert rules["meta"]["runnable"] == 0
        rule_id = rules["data"][0]["rule_id"]
        prop = client.get(f"/api/v1/prop/simulations/{run_id}", params={"rule_id": rule_id}).json()
        assert prop["meta"]["rule_locked"] is True
        assert "UNVERIFIED_RULES" in prop["data"]["labels"]


def test_missing_run_returns_semantic_404(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "api.db")) as client:
        response = client.get("/api/v1/runs/run_missing")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "run_not_found"
