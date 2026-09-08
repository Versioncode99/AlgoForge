"""The storage layout endpoints: what installation am I, and can I move off it?

The acceptance criterion behind these is blunt: AlgoForge must run with no
Obsidian installation, no vault, and no configuration, and an existing
vault-based installation must not break on the day that becomes true.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge.vault import LAYOUT_APP, LAYOUT_VAULT, default_root, write_pointer
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An app whose *repository* is also temporary, not only its workspace.

    Isolating the workspace is not enough for these tests. The pointer lives in
    the repository, so `migrate-layout` writes to `<repo>/config/storage.json` —
    and a fixture that leaves `main.ROOT` on the real checkout will happily
    rewrite the live installation's pointer at it.

    That is not hypothetical. An earlier version of this fixture did exactly
    that: a migration test repointed the real installation at a pytest
    temporary directory, and the next launch came up with an empty library
    while 413 strategies sat untouched in the vault it no longer knew about.
    """
    import forge_api.main as main

    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copytree(Path("rules"), repo / "rules", dirs_exist_ok=True)
    shutil.copy(Path("config") / "capabilities.json", repo / "config" / "capabilities.json")
    monkeypatch.setattr(main, "ROOT", repo)
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    with TestClient(create_app(tmp_path / "api.db")) as running:
        yield running


def test_a_fresh_install_reports_the_application_layout(client: Any) -> None:
    body = client.get("/api/v1/storage/layout").json()["data"]
    assert body["layout"] == LAYOUT_APP
    assert body["obsidian_required"] is False
    assert body["migration_available"] is False
    assert body["application_default"] == str(default_root())


def test_the_layout_report_counts_what_is_actually_there(client: Any) -> None:
    body = client.get("/api/v1/storage/layout").json()["data"]
    assert body["inventory"]["strategies"] == 0

    client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    after = client.get("/api/v1/storage/layout").json()["data"]
    assert after["inventory"]["strategies"] == 1


def test_an_export_destination_exists_without_a_vault(client: Any) -> None:
    """Obsidian is a place notes go, not a place state lives."""
    body = client.get("/api/v1/storage/layout").json()["data"]
    assert body["obsidian_export"]
    assert body["obsidian_export"].startswith(body["root"])


def test_a_relative_migration_target_is_refused(client: Any) -> None:
    response = client.post("/api/v1/storage/migrate-layout", json={"path": "somewhere"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "relative_path"


def test_migrating_a_workspace_onto_itself_is_refused(client: Any, tmp_path: Path) -> None:
    response = client.post(
        "/api/v1/storage/migrate-layout", json={"path": str(tmp_path / "workspace")}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "migration_not_verified"


def test_a_vault_installation_is_offered_the_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And it keeps working exactly as it did until the operator runs it."""
    import forge_api.main as main

    repo, vault = tmp_path / "repo", tmp_path / "vault"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)
    (repo / "config").mkdir()
    monkeypatch.setattr(main, "ROOT", repo)
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    write_pointer(repo, vault, layout=LAYOUT_VAULT)

    with TestClient(create_app(tmp_path / "api.db")) as running:
        body = running.get("/api/v1/storage/layout").json()["data"]
        assert body["layout"] == LAYOUT_VAULT
        assert body["migration_available"] is True
        # The store is still where the vault installation put it.
        assert body["store"].startswith(str(vault))


def test_migration_copies_and_leaves_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import forge_api.main as main

    repo, vault, target = tmp_path / "repo", tmp_path / "vault", tmp_path / "appdata"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)
    (repo / "config").mkdir()
    monkeypatch.setattr(main, "ROOT", repo)
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    write_pointer(repo, vault, layout=LAYOUT_VAULT)

    with TestClient(create_app(tmp_path / "api.db")) as running:
        created = running.post("/api/v1/strategies", json={"template": "momentum_breakout"})
        assert created.status_code == 201
        strategy_id = created.json()["data"]["strategy_id"]

        response = running.post("/api/v1/storage/migrate-layout", json={"path": str(target)})
        assert response.status_code == 200, response.text
        report = response.json()["data"]
        assert report["migrated"] is True
        assert report["before"]["strategies"] == report["after"]["strategies"] == 1

    # Copied, verified, switched — and the original is a complete backup.
    assert (target / "strategies" / strategy_id / "spec.json").exists()
    legacy = vault / "10 AlgoForge" / ".store" / "strategies" / strategy_id / "spec.json"
    assert legacy.exists(), "migration must never be destructive"

    # A restart now reads the application layout.
    with TestClient(create_app(tmp_path / "api2.db")) as restarted:
        body = restarted.get("/api/v1/storage/layout").json()["data"]
        assert body["layout"] == LAYOUT_APP
        assert body["root"] == str(target)
        assert body["inventory"]["strategies"] == 1
        rows = restarted.get("/api/v1/strategies").json()["data"]
        assert [row["strategy_id"] for row in rows] == [strategy_id]
