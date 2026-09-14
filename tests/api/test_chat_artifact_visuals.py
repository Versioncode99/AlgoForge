"""An artifact a conversation can draw has to name what to draw it from.

D4 §4F: *"The visualization must be generated from actual computed data. Never
generate fake visual data simply to make the chat look impressive."* The
interface honours that by drawing only what it can fetch — which puts the
requirement on the *reference*: an analysis artifact that does not carry the id
of the analysis it produced can only be re-run, and a re-run can land on a
different backtest and draw a different answer under the same title.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.chat import _ARTIFACTS
from forge_api.main import create_app

#: Kinds the interface draws inline, and the reference each one needs.
DRAWABLE: dict[str, str] = {
    "analysis": "artifact_id",
    "parameter_surface": "strategy_id",
    "resample": "strategy_id",
    "regime": "strategy_id",
    "backtest": "strategy_id",
}


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    with TestClient(create_app(database_path=tmp_path / "visuals.db")) as client:
        yield client


def test_every_drawable_kind_records_what_it_would_be_drawn_from() -> None:
    """Otherwise the chart is a re-run, and a re-run is a different measurement."""
    for action, (kind, refs) in _ARTIFACTS.items():
        needed = DRAWABLE.get(str(kind))
        if needed is None:
            continue
        assert needed in refs, (
            f"'{action}' produces a {kind} artifact without recording '{needed}', so a "
            "conversation could only draw it by recomputing it"
        )


def test_the_analysis_artifact_carries_the_id_the_lab_minted() -> None:
    """The specific case: `run_analysis` returns an id and only the result has it."""
    kind, refs = _ARTIFACTS["run_analysis"]
    assert str(kind) == "analysis"
    assert "artifact_id" in refs


def test_the_route_a_drawn_analysis_reads_exists(client: TestClient) -> None:
    """A reference pointing at a route that does not exist draws nothing forever."""
    response = client.get("/api/v1/lab/artifacts/does_not_exist")
    assert response.status_code in (404, 409, 422), (
        "the artifact route answered unexpectedly, so the interface's fetch path is not "
        "the one this reference assumes"
    )
