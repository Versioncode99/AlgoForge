"""The rule catalogue over HTTP, and the boundary around opening an account.

`forge.prop.catalogue` is tested on its own in
`tests/propdesk/test_prop_catalogue.py`. What is asserted here is the part that
only exists once it is wired: that the routes serve it, that the review state
survives the trip, and that the one verb which fixes a contract an account is
held to is not a verb an assistant can reach.

The last of those is the reason the read and the write were split. Opening a
prop account against a rule set decides which numbers the drawdown panel is
measured against for the life of that account. An assistant that could do it
from a file nobody had reviewed would be choosing the contract — so it can read
the catalogue and it cannot open an account, and `test_no_action_opens_a_prop
_account_from_a_rule_file` is what holds that apart.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from forge.prop.accounts import PropAccountStore
from forge.propdesk import PropDeskStore
from forge_api.propdesk import PropDeskService, build_propdesk_router

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)

RULE = {
    "rule_id": "desk-50k-funded",
    "display_name": "50K Funded",
    "phase": "FUNDED",
    "starting_balance": 50000,
    "maximum_loss": 2000,
    "trail_mode": "EOD",
    "floor_cap": 50000,
    "minimum_days": 5,
    "consistency_percent": 40,
}


@pytest.fixture
def rules(tmp_path: Path) -> Path:
    directory = tmp_path / "rules"
    directory.mkdir()
    (directory / "funded.json").write_text(json.dumps(RULE))
    (directory / "lapsed.json").write_text(
        json.dumps(
            {
                **RULE,
                "rule_id": "desk-50k-lapsed",
                "display_name": "50K Lapsed",
                "verified": True,
                "source_url": "https://example.test/terms",
                "review_expires_at": "2026-01-01",
            }
        )
    )
    return directory


@pytest.fixture
def service(tmp_path: Path, rules: Path) -> PropDeskService:
    return PropDeskService(
        store=PropDeskStore(tmp_path / "desk.db"),
        prop_accounts=PropAccountStore(tmp_path / "prop.db"),
        verdict_for=lambda strategy_id: None,
        rules_directory=rules,
        now=lambda: NOW,
    )


@pytest.fixture
def client(service: PropDeskService) -> TestClient:
    app = FastAPI()
    app.include_router(build_propdesk_router(service))
    return TestClient(app)


def data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ── reading the catalogue ────────────────────────────────────────────────────
def test_the_route_serves_the_rule_files_on_disk(client) -> None:
    payload = data(client.get("/api/v1/propdesk/rules"))
    assert {row["rule_id"] for row in payload["rule_sets"]} == {
        "desk-50k-funded",
        "desk-50k-lapsed",
    }
    assert payload["counts"]["rejected"] == 0


def test_the_review_state_survives_the_trip(client) -> None:
    """A lapsed verification must not arrive looking checked."""
    payload = data(client.get("/api/v1/propdesk/rules"))
    rows = {row["rule_id"]: row for row in payload["rule_sets"]}
    assert rows["desk-50k-lapsed"]["status"] == "EXPIRED"
    assert rows["desk-50k-lapsed"]["needs_review"] is True
    assert rows["desk-50k-funded"]["status"] == "UNVERIFIED"
    assert payload["counts"]["needing_review"] == 2


def test_the_route_carries_a_sentence_per_rule_set_that_needs_one(client) -> None:
    payload = data(client.get("/api/v1/propdesk/rules"))
    assert len(payload["warnings"]) == 2
    # Expired first: it is the more surprising of the two.
    assert "50K Lapsed" in payload["warnings"][0]
    assert "lapsed on 2026-01-01" in payload["warnings"][0]


def test_a_file_that_cannot_be_read_is_reported_rather_than_missing(
    client, rules: Path
) -> None:
    (rules / "broken.json").write_text(json.dumps({**RULE, "trail_mode": "RATCHET"}))
    payload = data(client.get("/api/v1/propdesk/rules"))
    assert payload["counts"]["rejected"] == 1
    assert "RATCHET" in payload["rejected"][0]["reason"]


def test_the_catalogue_is_read_fresh_rather_than_cached(client, rules: Path) -> None:
    """An operator who has just corrected a limit expects the correction.

    A cache keyed on nothing would serve the old numbers until a restart, and
    for a *limit* that is the wrong direction to be stale in.
    """
    before = data(client.get("/api/v1/propdesk/rules"))
    assert before["counts"]["loaded"] == 2
    (rules / "third.json").write_text(
        json.dumps({**RULE, "rule_id": "desk-25k", "display_name": "25K"})
    )
    after = data(client.get("/api/v1/propdesk/rules"))
    assert after["counts"]["loaded"] == 3


# ── opening an account ───────────────────────────────────────────────────────
def test_an_account_can_be_opened_against_a_rule_set(client, service) -> None:
    payload = data(client.post("/api/v1/propdesk/rules/desk-50k-funded/accounts"))
    assert payload["prop_account"]["rules"]["maximum_loss"] == 2000
    assert payload["prop_account"]["rules"]["trail_mode"] == "end_of_day"
    # And it is in the store, not merely returned.
    assert len(service.prop_accounts.all_accounts()) == 1


def test_opening_against_an_unreviewed_rule_set_says_so_at_the_moment_it_matters(
    client,
) -> None:
    """Not buried in a panel. This is when an operator decides what to trust."""
    payload = data(client.post("/api/v1/propdesk/rules/desk-50k-funded/accounts"))
    assert payload["needs_review"] is True
    assert "nobody has checked these numbers" in payload["warning"]


def test_opening_against_a_lapsed_rule_set_names_the_lapse(client) -> None:
    payload = data(client.post("/api/v1/propdesk/rules/desk-50k-lapsed/accounts"))
    assert payload["needs_review"] is True
    assert "lapsed on 2026-01-01" in payload["warning"]
    assert payload["rule_set"]["status"] == "EXPIRED"


def test_an_unknown_rule_id_is_refused_with_what_is_available(client) -> None:
    response = client.post("/api/v1/propdesk/rules/desk-nothing/accounts")
    assert response.status_code >= 400
    detail = response.text
    assert "desk-nothing" in detail
    # The refusal lists what it did find, so the operator can see the typo.
    assert "desk-50k-funded" in detail


def test_the_account_keeps_the_numbers_it_opened_with(client, service, rules) -> None:
    """A rule file edited later must not silently change an open account's contract.

    The account would then be held to terms nobody decided to move it to, which
    is the same failure as a rule set that reloads itself behind a drawdown
    panel.
    """
    data(client.post("/api/v1/propdesk/rules/desk-50k-funded/accounts"))
    (rules / "funded.json").write_text(json.dumps({**RULE, "maximum_loss": 5000}))
    account = service.prop_accounts.all_accounts()[0]
    assert account.rules.maximum_loss == 2000


# ── what the assistant may reach ─────────────────────────────────────────────
def registry(**overrides: object):
    """The action registry with nothing else configured.

    Every collaborator is `None` deliberately: what is under test is which verbs
    exist and what they are allowed to do, not what any of them would return.
    """
    from forge_api.actions import Actions

    return Actions(
        workspace=None, library=None, store=None, log=None, market=None, engine=None,
        agents=None, families=None, templates=None, mirror=None, workspaces=None,
        **overrides,
    )


def test_the_assistant_can_read_the_catalogue(rules: Path) -> None:
    payload = registry(rules_directory=rules).list_rule_sets()
    assert payload["counts"]["loaded"] == 2
    assert len(payload["warnings"]) == 2


def test_no_action_opens_a_prop_account_from_a_rule_file() -> None:
    """Reading is a verb; fixing a contract is not.

    Opening an account decides which numbers a desk holds it to for the life of
    the account. The HTTP route exists for the interface, and a person presses
    it. If this list ever gains such an action, that is a deliberate decision
    about what an assistant may choose, and this is where it gets noticed.
    """
    names = {row["name"] for row in registry().schemas()}
    assert "list_rule_sets" in names
    for forbidden in (
        "create_prop_account_from_rules",
        "open_prop_account",
        "load_rule_set",
    ):
        assert forbidden not in names


def test_reading_the_catalogue_is_not_a_mutating_action() -> None:
    """It writes nothing, so it needs no confirmation and no protection."""
    schema = next(
        row for row in registry().schemas() if row["name"] == "list_rule_sets"
    )
    assert schema["mutating"] is False
    assert schema["protected"] is False
    assert schema["requires_confirmation"] is False
