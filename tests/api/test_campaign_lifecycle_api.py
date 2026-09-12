"""Campaign lifecycle over HTTP: create, duplicate, archive, prioritise, export.

The store's own tests pin the decisions. These pin that the routes exist, carry
the right refusals, and that two campaigns can be started without either one
stopping the other — which was the whole point of removing the single-campaign
rule.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars using mechanisms that can be "
    "stated and falsified"
)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALGOFORGE_HOME", str(tmp_path))
    from forge_api.main import create_app

    return TestClient(create_app())


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _create(client: TestClient, name: str, **extra) -> dict:
    return _data(
        client.post(
            "/api/v1/campaigns",
            json={
                "name": name,
                "objective": OBJECTIVE,
                "dataset": "synthetic",
                "symbol": "MNQ",
                **extra,
            },
        )
    )


def test_a_campaign_carries_its_new_configuration(client: TestClient) -> None:
    campaign = _create(
        client, "NQ Alpha", priority=80, agent_target=4, description="What it is for",
        tags=["nq", "intraday"],
    )
    assert campaign["priority"] == 80
    assert campaign["agent_target"] == 4
    assert campaign["description"] == "What it is for"
    assert campaign["tags"] == ["intraday", "nq"]
    assert campaign["archived"] is False


def test_an_objective_that_says_nothing_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/campaigns",
        json={"name": "Lazy", "objective": "find alpha", "dataset": "synthetic"},
    )
    assert response.status_code == 400
    assert "objective" in response.text


def test_duplicating_copies_configuration_and_not_findings(client: TestClient) -> None:
    source = _create(client, "NQ Alpha", priority=70)
    copy = _data(
        client.post(f"/api/v1/campaigns/{source['campaign_id']}/duplicate", json={})
    )
    assert copy["name"] == "NQ Alpha (copy)"
    assert copy["priority"] == 70
    assert copy["parent_campaign_id"] == source["campaign_id"]
    assert copy["progress"]["experiments"] == 0
    assert copy["status"] == "created"


def test_archiving_hides_without_deleting_the_research(client: TestClient) -> None:
    campaign = _create(client, "Old work")
    _data(client.post(f"/api/v1/campaigns/{campaign['campaign_id']}/archive"))
    listed = _data(client.get("/api/v1/campaigns"))
    assert campaign["campaign_id"] not in {c["campaign_id"] for c in listed}
    # Still there, still readable — archiving is not deleting.
    assert _data(client.get(f"/api/v1/campaigns/{campaign['campaign_id']}"))["campaign"]
    _data(client.post(f"/api/v1/campaigns/{campaign['campaign_id']}/restore"))
    listed = _data(client.get("/api/v1/campaigns"))
    assert campaign["campaign_id"] in {c["campaign_id"] for c in listed}


def test_priority_and_rename_persist(client: TestClient) -> None:
    campaign = _create(client, "Before")
    _data(
        client.post(
            f"/api/v1/campaigns/{campaign['campaign_id']}/priority", json={"priority": 95}
        )
    )
    _data(
        client.post(
            f"/api/v1/campaigns/{campaign['campaign_id']}/rename", json={"name": "After"}
        )
    )
    reloaded = _data(client.get(f"/api/v1/campaigns/{campaign['campaign_id']}"))["campaign"]
    assert reloaded["priority"] == 95
    assert reloaded["name"] == "After"


def test_export_carries_the_research_map_and_not_the_evidence(client: TestClient) -> None:
    """A portable verdict is a verdict that can be edited in transit."""
    campaign = _create(client, "Exportable")
    payload = _data(client.get(f"/api/v1/campaigns/{campaign['campaign_id']}/export"))
    assert payload["campaign"]["name"] == "Exportable"
    assert payload["evidence_included"] is False
    assert "evidence stays where it was produced" in payload["note"]
    for key in ("frontier", "hypotheses", "validation", "agents", "sources", "skips"):
        assert key in payload
    # No verdict, backtest or holdout consumption travels.
    assert "verdicts" not in payload
    assert "backtests" not in payload


def test_two_campaigns_run_at_once_over_http(client: TestClient) -> None:
    first = _create(client, "NQ Alpha")
    second = _create(client, "Volatility Research")
    body = {"workers": 1, "cycle_seconds": 60.0, "max_bars": 2000}
    try:
        _data(client.post(f"/api/v1/campaigns/{first['campaign_id']}/start", json=body))
        _data(client.post(f"/api/v1/campaigns/{second['campaign_id']}/start", json=body))
        running = {c["name"] for c in _data(client.get("/api/v1/campaigns/running"))}
        assert running == {"NQ Alpha", "Volatility Research"}

        # Stopping one leaves the other researching. Stopping used to stop the
        # engine, because there was only ever one campaign and the two were the
        # same act.
        _data(client.post(f"/api/v1/campaigns/{second['campaign_id']}/stop"))
        running = {c["name"] for c in _data(client.get("/api/v1/campaigns/running"))}
        assert running == {"NQ Alpha"}
    finally:
        client.post("/api/v1/engine/stop")


def test_agents_are_deployed_and_capacity_is_reported_honestly(
    client: TestClient,
) -> None:
    campaign = _create(client, "Crewed")
    payload = _data(
        client.post(
            f"/api/v1/campaigns/{campaign['campaign_id']}/agents",
            json={"count": 10_000},
        )
    )
    assert payload["counts"]["total"] <= 64
    # Asking for more than can be served is answered, not silently truncated.
    assert payload["capacity_note"]
    assert "requested" in payload["capacity_note"]


def test_an_unknown_agent_role_names_the_valid_ones(client: TestClient) -> None:
    campaign = _create(client, "Crewed")
    response = client.post(
        f"/api/v1/campaigns/{campaign['campaign_id']}/agents",
        json={"count": 1, "roles": ["ASTROLOGY"]},
    )
    assert response.status_code == 400
    assert "FALSIFICATION" in response.text


def test_the_control_center_reports_validation_as_five_numbers(
    client: TestClient,
) -> None:
    """"Validation: 0" cannot distinguish "nothing was eligible" from
    "everything was tried and everything failed"."""
    _create(client, "NQ Alpha")
    payload = _data(client.get("/api/v1/campaigns/control-center"))
    validation = payload["validation"]
    assert set(validation) == {
        "attempts", "passed", "failed", "inconclusive", "blocked", "pending",
    }
    # Frontier states are reported with their zeroes, so a missing category
    # cannot look like an absent one.
    assert len(payload["frontier"]) >= 9
    assert payload["capacity"]["max_agents"] >= 1


def test_the_engine_diagnostics_answer_why_nothing_is_running(
    client: TestClient,
) -> None:
    payload = _data(client.get("/api/v1/engine/diagnostics"))
    assert payload["runtime"]["state"] == "STOPPED"
    assert payload["runtime"]["reason"]
    # `skips` is the accounting itself, with every kind and level present so a
    # missing category cannot read as an absent one.
    assert payload["skips"]["total"] == 0
    assert payload["skips"]["useful"] == 0
    assert payload["skips"]["wasted"] == 0
    assert len(payload["skips"]["by_kind"]) == 9
    assert len(payload["skips"]["by_level"]) == 8
    assert payload["recent_skips"] == []


def test_the_mechanism_total_does_not_count_one_idea_once_per_campaign(
    client: TestClient,
) -> None:
    """"Mechanisms" is the number that separates discovery from repetition.

    It is the count the campaign view leans on to say a hundred experiments
    against three explanations is a narrow search, and it was summed from each
    campaign's cached progress counter. That is wrong in two directions at
    once. The counter is refreshed when a campaign cycle runs, so between
    cycles the total lags the graph — which is what this test catches. And once
    refreshed, a mechanism is a *string* deduplicated by the graph, so three
    campaigns exploring one explanation each report 1 and sum to 3.

    Reading the store that owns the number is right for both.
    """
    from forge.research.frontier import SearchKind

    shared = (
        "Liquidity provision withdraws into the open, so resting size thins and the "
        "same order flow moves price further than it would in a normal book."
    )
    # The app's own graph, not a second connection to a guessed path: the route
    # is being asked what *it* can see.
    graph = client.app.state.actions.campaigns.hypotheses
    campaigns = [_create(client, f"Programme {n}") for n in range(3)]
    for index, campaign in enumerate(campaigns):
        graph.propose(
            campaign_id=campaign["campaign_id"],
            statement=f"Opening expansion is larger after compressed sessions ({index}).",
            mechanism=shared,
            prediction="Expansion exceeds the unconditional mean.",
            family="momentum",
            search_kind=SearchKind.HYPOTHESIS,
        )

    totals = _data(client.get("/api/v1/campaigns/control-center"))["totals"]
    assert totals["campaigns"] == 3

    # Each campaign, asked on its own, honestly reports the one explanation it
    # is exploring. Summing those is how "3" was arrived at.
    per_campaign = [graph.distinct_mechanisms(c["campaign_id"]) for c in campaigns]
    assert per_campaign == [1, 1, 1]
    assert sum(per_campaign) == 3

    assert totals["mechanisms"] == 1, (
        "three campaigns exploring one explanation did not report one mechanism"
    )

    # A second, genuinely different explanation does move the number.
    graph.propose(
        campaign_id=campaigns[0]["campaign_id"],
        statement="Session volume concentrates into the close on expiry days.",
        mechanism="Index rebalancing forces size into the closing auction.",
        prediction="Closing-auction share is higher on expiry days.",
        family="flow",
        search_kind=SearchKind.HYPOTHESIS,
    )
    assert _data(client.get("/api/v1/campaigns/control-center"))["totals"]["mechanisms"] == 2
