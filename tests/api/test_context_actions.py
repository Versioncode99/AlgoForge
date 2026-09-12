"""Context and instruments through the action registry.

The point of these is the same as the campaign ones: the command palette, a
keyboard shortcut and an assistant must reach the same verb the interface does.
They also pin the two refusals that keep the context honest -- an instrument
that does not exist, and a context set on a group no panel belongs to.
"""

from __future__ import annotations

import pytest
from forge_api.actions import ActionError


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_HOME", str(tmp_path))
    from forge_api.main import create_app

    return create_app()


@pytest.fixture
def actions(app):
    registry = app.state.actions
    # A context belongs to a workspace, so the tests need one open. Built
    # through the registry rather than the store so the fixture exercises the
    # same path the interface takes.
    registry.call(
        "create_workspace",
        {"name": "NQ Lab", "template_key": "quant_researcher"},
    )
    return registry


def test_the_catalogue_is_searchable_and_says_what_is_verified(actions) -> None:
    listed = actions.list_instruments()
    assert listed["count"] >= 12
    assert listed["verified"] < listed["count"], "expected some unverified specifications"
    assert {row["specification"] for row in listed["instruments"]} == {"verified", "unverified"}


def test_searching_puts_the_exact_root_first(actions) -> None:
    """Somebody typing NQ means NQ, not MNQ."""
    matches = actions.call("search_instruments", {"query": "NQ"})["matches"]
    assert matches[0]["root"] == "NQ"
    assert {"MNQ"} <= {row["root"] for row in matches}


def test_searching_matches_description_and_exchange(actions) -> None:
    assert actions.call("search_instruments", {"query": "gold"})["matches"]
    assert actions.call("search_instruments", {"query": "comex"})["matches"]


def test_an_instrument_that_does_not_exist_is_refused_by_name(actions) -> None:
    """Nothing is invented. §6: do not invent instruments."""
    with pytest.raises(ActionError) as exc:
        actions.call("set_context", {"instrument": "TSLA"})
    assert "NQ" in str(exc.value)


def test_setting_a_context_reports_how_many_panels_follow_it(actions) -> None:
    result = actions.call("set_context", {"instrument": "NQ", "timeframe": "1m"})
    assert result["context"]["instrument"] == "NQ"
    assert "following" in result
    assert result["note"]


def test_a_context_on_a_group_no_panel_belongs_to_says_so(actions) -> None:
    """It looks exactly like a context that did nothing, because it did."""
    result = actions.call("set_context", {"instrument": "NQ", "group": "nobody-here"})
    assert result["following"] == []
    assert "No panel belongs to this group" in result["note"]


def test_an_unverified_specification_is_flagged_when_it_becomes_context(actions) -> None:
    """An unverified multiplier is the number a position would be sized from."""
    result = actions.call("set_context", {"instrument": "RTY"})
    assert "specification_warning" in result
    assert "not individually verified" in result["specification_warning"]

    verified = actions.call("set_context", {"instrument": "NQ"})
    assert "specification_warning" not in verified


def test_set_context_with_nothing_to_set_is_refused(actions) -> None:
    with pytest.raises(ActionError):
        actions.call("set_context", {})


def test_describing_the_context_says_where_each_panel_gets_its_symbol(actions) -> None:
    actions.call("set_context", {"instrument": "NQ"})
    described = actions.call("describe_context", {})
    assert described["context"]["instrument"] == "NQ"
    assert isinstance(described["panels"], list)
    for panel in described["panels"]:
        assert panel["source"] in {"panel", "context"}


def test_clearing_one_facet_leaves_the_rest(actions) -> None:
    actions.call("set_context", {"instrument": "NQ", "timeframe": "1m"})
    cleared = actions.call("clear_context", {"facet": "instrument"})
    assert cleared["context"]["instrument"] == ""
    assert cleared["context"]["timeframe"] == "1m"


def test_an_unknown_facet_is_refused_and_lists_the_real_ones(actions) -> None:
    with pytest.raises(ActionError) as exc:
        actions.call("clear_context", {"facet": "colour"})
    assert "instrument" in str(exc.value)


def test_a_campaign_facet_must_name_a_campaign_that_exists(actions) -> None:
    with pytest.raises(ActionError):
        actions.call("set_context", {"campaign_id": "no-such-campaign"})


def test_context_verbs_are_registered_and_none_is_protected(actions) -> None:
    schemas = {s["name"]: s for s in actions.schemas()}
    for name in (
        "list_instruments",
        "search_instruments",
        "describe_context",
        "set_context",
        "clear_context",
    ):
        assert name in schemas, f"{name} is not registered"
        assert schemas[name]["protected"] is False


# ── data health ──────────────────────────────────────────────────────────────


def test_data_health_reports_services_as_well_as_archives(actions) -> None:
    """The gap this closes: an operator could see a hole in the 2019 archive and
    could not find out that Databento had been refusing since lunchtime."""
    health = actions.call("data_health", {})
    assert set(health) == {"datasets", "services", "calendars", "absent", "tiers"}
    assert health["services"], "no service rows at all"


def test_a_source_nobody_has_called_is_listed_and_is_not_healthy(actions) -> None:
    health = actions.call("data_health", {})
    states = {row["name"]: row["state"] for row in health["services"]}
    # Declared up front, so a panel can show that the source you are waiting on
    # has never been tried rather than omitting it.
    assert "crypto-public" in states
    assert states["crypto-public"] in {"NOT_OBSERVED", "UNCONFIGURED"}
    assert "HEALTHY" not in {states["crypto-public"]}


def test_every_service_row_carries_a_remedy(actions) -> None:
    for row in actions.call("data_health", {})["services"]:
        assert set(row["explain"]) == {"what", "why", "impact", "remedy"}


def test_the_missing_news_feed_is_stated_rather_than_omitted(actions) -> None:
    """A reader finding no news section concludes the feed is healthy and quiet."""
    absent = {row["capability"]: row for row in actions.call("data_health", {})["absent"]}
    assert "Headline news" in absent
    assert "no headline news feed" in absent["Headline news"]["what"].lower()


def test_data_health_is_read_only_and_unprotected(actions) -> None:
    schema = {s["name"]: s for s in actions.schemas()}["data_health"]
    assert schema["mutating"] is False
    assert schema["protected"] is False


def test_the_tier_ladder_is_published_with_what_each_tier_means(actions) -> None:
    tiers = actions.call("data_health", {})["tiers"]
    assert next(row["tier"] for row in tiers) == "EXECUTION_GRADE"
    assert all(row["means"] for row in tiers)
