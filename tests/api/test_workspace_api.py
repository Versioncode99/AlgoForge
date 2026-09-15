"""The panel picker's list, and the drift it used to hide.

The interface held its own array of panel kinds. It offered twenty-three; the
build renders thirty-five. So the approvals queue, the audit trail, the
pre-trade gate, the portfolio and the desk's own eight panels were implemented,
worked when a workspace template seeded one, and could not be added by anybody
who wanted one — which looks exactly like a feature that was never built.

The list is served now, and what is pinned here is that the server's answer is
the whole enum. A route that filtered it, or a picker that kept a copy, would
put the two back out of step; the difference is that this time it would fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from forge.workstation.models import PanelKind

REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from forge_api.main import create_app

    return TestClient(create_app(tmp_path / "workspaces.db"))


def data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_route_serves_every_panel_kind_this_build_renders(client: TestClient) -> None:
    served = data(client.get("/api/v1/workspaces/panel-kinds"))["panel_kinds"]
    assert set(served) == {kind.value for kind in PanelKind}
    assert len(served) == len(set(served)), "a kind is served twice"


def test_the_interface_keeps_no_list_of_its_own(client: TestClient) -> None:
    """The failure this prevents is silent and one-directional.

    A copy in the client can only ever be missing kinds the server gained, and a
    missing kind is a panel nobody can add — indistinguishable, from the outside,
    from a panel that does not exist.
    """
    source = (REPOSITORY / "apps" / "web" / "src" / "views" / "Workspace.tsx").read_text(
        encoding="utf-8"
    )
    # The literal array that used to live here. Any re-introduction of a
    # multi-entry list of quoted kinds beside the picker is the thing to catch.
    assert "const PANEL_KINDS" not in source
    assert "/workspaces/panel-kinds" in source, (
        "the picker no longer reads the served list"
    )


#: Panel kinds the registry declares and no view draws. They render the honest
#: "registered but has no view yet" panel rather than something plausible, which
#: is the right behaviour — but the *number* of them should be a decision rather
#: than a drift, so it is written down.
#:
#: Building one of these makes this list shrink and this test fail, which is the
#: moment to delete its entry. Adding a sixth is the moment to ask whether the
#: kind should be registered before it can be drawn.
UNBUILT = {"evidence", "lineage", "notes", "research_library", "validation"}


def test_the_kinds_with_no_view_are_exactly_the_ones_declared_here(
    client: TestClient,
) -> None:
    served = data(client.get("/api/v1/workspaces/panel-kinds"))["panel_kinds"]
    body = (REPOSITORY / "apps" / "web" / "src" / "components" / "PanelBody.tsx").read_text(
        encoding="utf-8"
    )
    drawn = set(re.findall(r"case '([a-z_]+)':", body))
    undrawn = {kind for kind in served if kind not in drawn}
    assert undrawn == UNBUILT


def test_a_kind_with_no_view_says_so_rather_than_drawing_something_else(
    client: TestClient,
) -> None:
    """The honest fallback is what makes the list above tolerable.

    A panel that rendered an empty chart, or the nearest kind it did know, would
    be the failure; saying "registered but has no view yet" is not.
    """
    body = (REPOSITORY / "apps" / "web" / "src" / "components" / "PanelBody.tsx").read_text(
        encoding="utf-8"
    )
    assert "This panel kind is registered but has no view yet." in body
    assert "<NotBuilt" in body


def test_a_served_kind_can_actually_be_added(client: TestClient) -> None:
    """The list is only worth serving if the kinds on it are accepted."""
    created = data(
        client.post(
            "/api/v1/actions/create_workspace",
            json={"arguments": {"name": "Picker", "activate": True}},
        )
    )
    assert created

    served = data(client.get("/api/v1/workspaces/panel-kinds"))["panel_kinds"]
    # One of the twelve the old hard-coded list left out.
    assert "approvals" in served
    added = client.post(
        "/api/v1/actions/add_panel", json={"arguments": {"kind": "approvals"}}
    )
    assert added.status_code == 200, added.text


def test_an_unknown_kind_is_still_refused(client: TestClient) -> None:
    """Serving the list must not have turned the field into free text."""
    data(
        client.post(
            "/api/v1/actions/create_workspace",
            json={"arguments": {"name": "Picker", "activate": True}},
        )
    )
    response = client.post(
        "/api/v1/actions/add_panel", json={"arguments": {"kind": "not_a_panel"}}
    )
    assert response.status_code >= 400
