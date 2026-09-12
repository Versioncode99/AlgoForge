"""Campaigns through the action registry.

Campaigns had thirty-odd HTTP routes and no presence in the action registry at
all, which meant the interface could start a campaign and an assistant could
not — not because it was denied, but because the verb did not exist in the only
vocabulary it has. These tests pin the verbs, and pin that they take the *same*
path as the routes rather than a second implementation that can drift.

Driven through the real application so the registry under test is the one the
process actually serves, with its campaign service attached by `control`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from forge.modes.permissions import Actor
from forge_api.actions import ActionError, ActionRisk

OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars using mechanisms that can be "
    "stated and falsified"
)

#: Everything a campaign screen, a command palette and an assistant all need.
LIFECYCLE = (
    "list_campaigns",
    "describe_campaign",
    "create_campaign",
    "start_campaign",
    "pause_campaign",
    "resume_campaign",
    "stop_campaign",
    "prioritise_campaign",
    "rename_campaign",
    "duplicate_campaign",
    "archive_campaign",
    "campaign_agents",
    "deploy_campaign_agents",
    "campaign_skips",
    "campaign_frontier",
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_HOME", str(tmp_path))
    from forge_api.main import create_app

    application = create_app()
    return application


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def actions(app):
    return app.state.actions


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _new(actions, name: str = "NQ Momentum", **extra) -> str:
    result = actions.call(
        "create_campaign",
        {"name": name, "objective": OBJECTIVE, "dataset": "synthetic", **extra},
    )
    return result["campaign"]["campaign_id"]


def test_every_lifecycle_verb_is_registered(actions) -> None:
    missing = [name for name in LIFECYCLE if name not in actions.names()]
    assert not missing, f"campaign verbs absent from the registry: {missing}"


def test_the_registry_is_reachable_over_http_with_the_campaign_verbs(client) -> None:
    """The palette reads this listing. If a verb is not here it cannot be offered."""
    schemas = _data(client.get("/api/v1/actions"))
    names = {schema["name"] for schema in schemas}
    assert set(LIFECYCLE) <= names


def test_creating_does_not_start(actions) -> None:
    """"Create and start" reads as one act in a sentence and is two here."""
    result = actions.call(
        "create_campaign",
        {"name": "NQ Momentum", "objective": OBJECTIVE, "dataset": "synthetic"},
    )
    assert result["campaign"]["status"] == "created"
    assert "not running" in result["note"]


def test_an_objective_that_says_nothing_is_refused(actions) -> None:
    with pytest.raises(ActionError):
        actions.call(
            "create_campaign",
            {"name": "Empty", "objective": "stuff", "dataset": "synthetic"},
        )


def test_an_unknown_campaign_names_the_ones_that_exist(actions) -> None:
    """A refusal that does not help the caller recover is half a refusal."""
    known = _new(actions, "Real campaign")
    with pytest.raises(ActionError) as exc:
        actions.call("describe_campaign", {"campaign_id": "no-such-campaign"})
    assert known in str(exc.value)


def test_starting_through_the_action_is_visible_over_http(actions, client) -> None:
    """One path, two surfaces.

    This is the whole point of the change: an assistant starting a campaign and
    an operator starting it from the screen must produce the same state, because
    they are the same call. A second implementation for the agent is how the two
    come to disagree about what "start" means.
    """
    campaign_id = _new(actions, "NQ Momentum", agent_target=2)
    actions.call("start_campaign", {"campaign_id": campaign_id, "workers": 2})
    try:
        over_http = _data(client.get(f"/api/v1/campaigns/{campaign_id}"))
        assert over_http["campaign"]["status"] == "running"
        # The crew the campaign asked for was deployed by the same handshake.
        assert over_http["agents"]["roster"], "starting deployed no agents"
    finally:
        actions.call("stop_campaign", {"campaign_id": campaign_id})


def test_starting_over_http_is_visible_through_the_action(actions, client) -> None:
    """And the converse, so neither surface is the privileged one."""
    campaign = _data(
        client.post(
            "/api/v1/campaigns",
            json={"name": "Over HTTP", "objective": OBJECTIVE, "dataset": "synthetic"},
        )
    )
    campaign_id = campaign["campaign_id"]
    _data(client.post(f"/api/v1/campaigns/{campaign_id}/start", json={"workers": 1}))
    try:
        described = actions.call("describe_campaign", {"campaign_id": campaign_id})
        assert described["campaign"]["status"] == "running"
    finally:
        actions.call("stop_campaign", {"campaign_id": campaign_id})


def test_pausing_keeps_the_crew_and_stopping_releases_it(actions) -> None:
    campaign_id = _new(actions, "NQ Momentum", agent_target=2)
    actions.call("start_campaign", {"campaign_id": campaign_id, "workers": 1})
    deployed = len(actions.call("campaign_agents", {"campaign_id": campaign_id})["roster"])
    assert deployed

    paused = actions.call("pause_campaign", {"campaign_id": campaign_id})
    assert paused["campaign"]["status"] == "paused"
    # Pausing holds the campaign without ending it: the crew survives, which is
    # what makes resuming cheap and what distinguishes it from stopping.
    assert len(actions.call("campaign_agents", {"campaign_id": campaign_id})["roster"]) == deployed

    actions.call("resume_campaign", {"campaign_id": campaign_id, "workers": 1})
    assert actions.call("describe_campaign", {"campaign_id": campaign_id})["campaign"][
        "status"
    ] == "running"
    actions.call("stop_campaign", {"campaign_id": campaign_id})


def test_priority_allocates_workers_and_says_so(actions) -> None:
    """Priority must not read as a research quality dial."""
    campaign_id = _new(actions)
    result = actions.call("prioritise_campaign", {"campaign_id": campaign_id, "priority": 90})
    assert result["campaign"]["priority"] == 90
    note = result["note"].lower()
    assert "worker" in note
    assert "gate" in note or "evidence" in note


def test_priority_outside_the_range_is_refused_not_clamped(actions) -> None:
    campaign_id = _new(actions)
    with pytest.raises(ActionError):
        actions.call("prioritise_campaign", {"campaign_id": campaign_id, "priority": 400})


def test_archiving_needs_the_operator_to_mean_it(actions) -> None:
    """An archive holds work somebody did, so it is not a SAFE verb."""
    schema = {s["name"]: s for s in actions.schemas()}["archive_campaign"]
    assert schema["risk"] == str(ActionRisk.CONFIRM)
    assert schema["requires_confirmation"] is True


def test_no_campaign_verb_is_protected_and_none_is_high_risk(actions) -> None:
    """A campaign allocates research effort. It reaches no account and no money.

    Pinned as a property rather than left to reading: if a campaign verb ever
    becomes a route to an execution control, this test is what notices.
    """
    schemas = {s["name"]: s for s in actions.schemas()}
    for name in LIFECYCLE:
        assert schemas[name]["protected"] is False, f"{name} became protected"
        assert schemas[name]["risk"] != str(ActionRisk.HIGH), f"{name} became HIGH risk"


def test_an_assistant_may_run_research_and_still_cannot_confirm_for_the_operator(
    actions,
) -> None:
    """The AI gets the research verbs and not the operator's confirmation.

    `confirmed` is the operator's answer, not the caller's opinion, and an agent
    setting it on its own behalf is the escape route the boundary exists to
    close.
    """
    campaign_id = _new(actions, "Agent started")
    # Read and start are available to an AI actor: campaigns are research.
    listed = actions.call("list_campaigns", {}, actor=Actor.AI, origin="test")
    assert any(row["campaign_id"] == campaign_id for row in listed["campaigns"])

    with pytest.raises(ActionError):
        actions.call("archive_campaign", {"campaign_id": campaign_id}, actor=Actor.AI)


def test_refusal_accounting_is_reachable_by_kind(actions) -> None:
    """The replacement for one number called "Skipped by memory"."""
    campaign_id = _new(actions)
    skips = actions.call("campaign_skips", {"campaign_id": campaign_id})
    assert "counts" in skips and isinstance(skips["counts"], dict)
    # Nothing has run, so every kind is zero — and every kind is *present*.
    # A kind missing from the payload renders as an absent category, and
    # "nothing was refused for this reason" is not "we did not look".
    assert skips["counts"], "skip accounting returned no categories at all"
    assert skips["skips"] == []


def test_the_frontier_reports_every_state_including_the_empty_ones(actions) -> None:
    campaign_id = _new(actions)
    frontier = actions.call("campaign_frontier", {"campaign_id": campaign_id})
    assert set(frontier["counts"]) >= {"UNKNOWN", "UNTESTED", "VALIDATED", "FAILED"}


def test_an_unknown_frontier_state_is_refused_by_name(actions) -> None:
    campaign_id = _new(actions)
    with pytest.raises(ActionError) as exc:
        actions.call("campaign_frontier", {"campaign_id": campaign_id, "state": "GREAT"})
    assert "PROMISING" in str(exc.value)


def test_deploying_more_agents_than_the_machine_serves_says_so(actions) -> None:
    """Agents that would queue without researching are not agents."""
    from forge.research.agents import MAX_AGENTS

    campaign_id = _new(actions)
    result = actions.call(
        "deploy_campaign_agents", {"campaign_id": campaign_id, "count": MAX_AGENTS}
    )
    assert result["count"] == len(result["deployed"])
    if result["count"] < MAX_AGENTS:
        assert result["capacity_note"], "the shortfall was silent"


def test_an_unknown_role_is_refused_and_lists_the_real_ones(actions) -> None:
    campaign_id = _new(actions)
    with pytest.raises(ActionError) as exc:
        actions.call(
            "deploy_campaign_agents",
            {"campaign_id": campaign_id, "count": 1, "roles": ["oracle"]},
        )
    assert "DISCOVERY" in str(exc.value)


def test_duplicating_copies_configuration_and_not_findings(actions) -> None:
    source = _new(actions, "Source", priority=70)
    copy = actions.call("duplicate_campaign", {"campaign_id": source})
    assert copy["copied_from"] == source
    assert copy["campaign"]["campaign_id"] != source
    assert copy["campaign"]["priority"] == 70
    assert copy["campaign"]["status"] == "created"
    frontier = actions.call(
        "campaign_frontier", {"campaign_id": copy["campaign"]["campaign_id"]}
    )
    assert frontier["items"] == []
