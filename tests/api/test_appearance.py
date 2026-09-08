"""Appearance settings: stored on the server, and refused when unknown.

Three things are load-bearing.

*The default is Premium Graphite.* A fresh install, a settings file that will not
parse, and a settings file written before appearance existed all have to arrive
at the same interface.

*A value the stylesheet has no palette for is refused rather than stored.* A
theme key that persisted through a rename would leave the application unstyled
with nothing on screen to say why, and the only place to notice would be the
JSON file.

*Changing appearance changes nothing else.* The settings routes rebuild the
whole `Settings` object, so a field not carried through silently reverts to its
default — which for model routing means a strategy suddenly being written by a
different model.
"""

from __future__ import annotations

import json
import pathlib
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app
from forge_api.settings_store import ACCENTS, DENSITIES, MOTIONS, THEMES


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def read(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/settings/appearance")
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


# ── defaults ─────────────────────────────────────────────────────────────────


def test_the_default_is_premium_graphite_compact_and_silent(client: TestClient) -> None:
    data = read(client)
    assert data["theme"] == "graphite"
    assert data["accent"] == "silver"
    # A workstation is dense by default; making the labels bigger is how a dense
    # interface becomes a spreadsheet with fewer rows on it.
    assert data["density"] == "compact"
    assert data["motion"] == "standard"
    # Off until asked for. A sound nobody requested is worse than no sound.
    assert data["sound_enabled"] is False


def test_the_options_travel_with_the_value(client: TestClient) -> None:
    """So the interface never keeps its own list of themes to drift from."""
    options = read(client)["options"]
    assert [item["key"] for item in options["themes"]] == [item["key"] for item in THEMES]
    assert {item["key"] for item in options["accents"]} == {item["key"] for item in ACCENTS}
    assert {item["key"] for item in options["densities"]} == {item["key"] for item in DENSITIES}
    assert {item["key"] for item in options["motions"]} == {item["key"] for item in MOTIONS}
    for group in options.values():
        for item in group:
            assert item["label"] and item["detail"], "an option with no detail is a guess"


def test_the_three_themes_the_brief_asks_for_exist(client: TestClient) -> None:
    keys = {item["key"] for item in read(client)["options"]["themes"]}
    assert {"graphite", "silver", "contrast"} <= keys


# ── persistence ──────────────────────────────────────────────────────────────


def test_a_change_persists_across_a_restart(client: TestClient, tmp_path: Any) -> None:
    """Server-side, so a second machine finds the workstation you configured."""
    client.patch(
        "/api/v1/settings/appearance",
        json={"theme": "silver", "density": "comfortable", "sound_enabled": True},
    )
    with TestClient(create_app(database_path=tmp_path / "test.db")) as second:
        data = read(second)
    assert data["theme"] == "silver"
    assert data["density"] == "comfortable"
    assert data["sound_enabled"] is True


def test_a_partial_update_leaves_everything_else_alone(client: TestClient) -> None:
    client.patch("/api/v1/settings/appearance", json={"theme": "contrast", "accent": "amber"})
    client.patch("/api/v1/settings/appearance", json={"density": "comfortable"})
    data = read(client)
    assert data["theme"] == "contrast"
    assert data["accent"] == "amber"
    assert data["density"] == "comfortable"


def test_the_volume_is_kept_and_clamped(client: TestClient) -> None:
    client.patch("/api/v1/settings/appearance", json={"sound_volume": 0.8})
    assert read(client)["sound_volume"] == pytest.approx(0.8)
    # Outside the range is a bad request rather than a silently clamped one.
    assert client.patch("/api/v1/settings/appearance", json={"sound_volume": 4}).status_code == 422
    assert client.patch("/api/v1/settings/appearance", json={"sound_volume": -1}).status_code == 422
    assert read(client)["sound_volume"] == pytest.approx(0.8)


# ── refusal ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("theme", "neon"),
        ("accent", "magenta"),
        ("density", "enormous"),
        ("motion", "bouncy"),
    ],
)
def test_a_value_with_no_palette_behind_it_is_refused(
    client: TestClient, field: str, value: str
) -> None:
    response = client.patch("/api/v1/settings/appearance", json={field: value})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == f"unknown_{field}"
    assert detail["known"], "a refusal has to say what is allowed"
    # And nothing was stored.
    assert read(client)[field] != value


def test_a_stored_value_that_no_longer_exists_falls_back(
    client: TestClient, tmp_path: Any
) -> None:
    """A theme key that survives a rename must not leave the app unstyled."""
    path = next(tmp_path.rglob("settings.json"), None)
    if path is None:
        client.patch("/api/v1/settings/appearance", json={"theme": "silver"})
        path = next(tmp_path.rglob("settings.json"))
    raw = json.loads(path.read_text("utf-8"))
    raw["appearance"] = {**raw.get("appearance", {}), "theme": "a_theme_that_was_deleted"}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with TestClient(create_app(database_path=tmp_path / "test.db")) as second:
        assert read(second)["theme"] == "graphite"


# ── appearance is not allowed to disturb anything else ───────────────────────


def test_changing_the_theme_does_not_reset_model_routing(client: TestClient) -> None:
    before = client.get("/api/v1/settings").json()["data"]["ai"]["routing"]
    client.patch("/api/v1/settings/appearance", json={"theme": "contrast"})
    after = client.get("/api/v1/settings").json()["data"]["ai"]["routing"]
    assert after == before


def test_changing_model_routing_does_not_reset_the_theme(client: TestClient) -> None:
    """`Settings` is rebuilt wholesale by the AI patch, so this is a real risk."""
    client.patch("/api/v1/settings/appearance", json={"theme": "silver", "accent": "ice"})
    client.patch("/api/v1/settings", json={"ai_enabled": False})
    data = read(client)
    assert data["theme"] == "silver"
    assert data["accent"] == "ice"


def test_the_main_settings_document_carries_the_appearance(client: TestClient) -> None:
    client.patch("/api/v1/settings/appearance", json={"theme": "contrast"})
    data = client.get("/api/v1/settings").json()["data"]
    assert data["appearance"]["theme"] == "contrast"
    assert {item["key"] for item in data["appearance_options"]["themes"]} >= {"graphite"}
